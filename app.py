import io
import json
import os
import shutil
from pathlib import Path
from typing import Iterator, List, Tuple

import streamlit as st
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter
from pdf2image import convert_from_bytes
import pytesseract
from streamlit.components.v1 import html

MAX_PAGES_PER_CHUNK = 8
PDF2IMAGE_THREAD_LIMIT = max(1, min(4, os.cpu_count() or 1))
FAST_MODE_MAX_DPI = 220
FAST_TESSERACT_CONFIG = "--oem 3 --psm 6"
DEFAULT_TESSERACT_CONFIG = ""


def detect_default_tesseract_cmd() -> str:
    """Return a reasonable default path for the Tesseract executable."""
    env_paths = (
        os.environ.get("TESSERACT_CMD"),
        os.environ.get("TESSERACT_PATH"),
    )
    for env_path in env_paths:
        if env_path:
            return env_path

    detected = shutil.which("tesseract")
    if detected:
        return detected

    linux_path = Path("/usr/bin/tesseract")
    if linux_path.exists():
        return str(linux_path)

    windows_path = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
    if windows_path.exists():
        return str(windows_path)

    return ""


def configure_tesseract(cmd_path: str) -> None:
    """Optionally configure pytesseract with an explicit executable path."""
    if cmd_path:
        tesseract_executable = Path(cmd_path).expanduser()
        if tesseract_executable.exists():
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_executable)
        else:
            st.warning(f"Executavel nao encontrado em: {tesseract_executable}")


def get_tesseract_config(fast_mode: bool) -> str:
    return FAST_TESSERACT_CONFIG if fast_mode else DEFAULT_TESSERACT_CONFIG


@st.cache_data(show_spinner=False)
def load_pdf_bytes(uploaded_file) -> bytes:
    return uploaded_file.getvalue()


@st.cache_data(show_spinner=False)
def get_pdf_page_count(file_bytes: bytes) -> int:
    reader = PdfReader(io.BytesIO(file_bytes))
    return len(reader.pages)


def build_page_selection(page_count: int) -> List[int]:
    page_numbers = list(range(1, page_count + 1))
    selected = st.multiselect(
        "Selecione as paginas para processar",
        options=page_numbers,
        default=page_numbers,
        format_func=lambda x: f"Pagina {x}",
    )
    return selected or []


def iter_pdf_images(file_bytes: bytes, pages: List[int], dpi: int) -> Iterator[Tuple[int, Image.Image]]:
    if not pages:
        return

    unique_pages: List[int] = []
    seen = set()
    for page in pages:
        if page not in seen:
            unique_pages.append(page)
            seen.add(page)

    if not unique_pages:
        return

    def contiguous_chunks(numbers: List[int]) -> Iterator[List[int]]:
        chunk: List[int] = [numbers[0]]
        for number in numbers[1:]:
            if number == chunk[-1] + 1 and len(chunk) < MAX_PAGES_PER_CHUNK:
                chunk.append(number)
            else:
                yield chunk
                chunk = [number]
        yield chunk

    ordered = sorted(unique_pages)

    for chunk in contiguous_chunks(ordered):
        first = chunk[0]
        last = chunk[-1]
        thread_count = min(len(chunk), PDF2IMAGE_THREAD_LIMIT)
        chunk_images = convert_from_bytes(
            file_bytes,
            dpi=dpi,
            first_page=first,
            last_page=last,
            grayscale=True,
            thread_count=thread_count,
        )
        for offset, page_number in enumerate(chunk):
            yield page_number, chunk_images[offset]
        chunk_images.clear()


def ocr_pdf_pages(file_bytes: bytes, pages: List[int], dpi: int, lang: str, config: str) -> List[Tuple[int, str]]:
    results: List[Tuple[int, str]] = []
    for page_number, image in iter_pdf_images(file_bytes, pages, dpi):
        try:
            with st.spinner(f"Executando OCR na pagina {page_number}..."):
                text = pytesseract.image_to_string(image, lang=lang, config=config)
        finally:
            try:
                image.close()
            except Exception:
                pass
        results.append((page_number, text))
    return results


def build_searchable_pdf(file_bytes: bytes, pages: List[int], dpi: int, lang: str, config: str) -> io.BytesIO:
    writer = PdfWriter()
    for page_number, image in iter_pdf_images(file_bytes, pages, dpi):
        try:
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(image, extension="pdf", lang=lang, config=config)
        finally:
            try:
                image.close()
            except Exception:
                pass
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer.add_page(reader.pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def auto_click_button(label: str) -> None:
    """Trigger a click on the first Streamlit button matching the provided label."""
    html(
        f"""
        <script>
        const targetLabel = {json.dumps(label.strip())};
        const streamlitDoc = window.parent.document;
        const buttons = streamlitDoc.querySelectorAll("button");
        for (const button of buttons) {{
            if (button.innerText && button.innerText.trim() === targetLabel) {{
                button.click();
                break;
            }}
        }}
        </script>
        """,
        height=0,
    )


def display_ocr_output(results: List[Tuple[int, str]]) -> None:
    for page_number, text in results:
        st.subheader(f"Resultado - Pagina {page_number}")
        st.text_area(
            label=f"OCR pagina {page_number}",
            value=text,
            height=min(400, max(120, len(text) // 2)),
            key=f"text_page_{page_number}",
        )


def main() -> None:
    st.set_page_config(page_title="OCR com Tesseract", page_icon=":memo:", layout="wide")
    st.title("OCR de PDFs e Imagens com Tesseract")

    st.sidebar.header("Configuracoes")
    tesseract_cmd = st.sidebar.text_input(
        "Caminho do executavel do Tesseract",
        value=detect_default_tesseract_cmd(),
        help="Informe apenas se o Tesseract nao estiver no PATH. Exemplo: C:/Program Files/Tesseract-OCR/tesseract.exe",
    )
    configure_tesseract(tesseract_cmd)

    lang = st.sidebar.text_input(
        "Idiomas Tesseract",
        value="por",
        help="Use os codigos Tesseract separados por '+', ex.: 'por+eng'. Certifique-se de que os pacotes estejam instalados.",
    )
    dpi = st.sidebar.slider("Resolucao (DPI) para conversao de paginas", 150, 400, 250, step=50)
    fast_mode = st.sidebar.checkbox(
        "Modo rapido (menor precisao)",
        value=False,
        help="Reduz o DPI e usa configuracoes mais rapidas do Tesseract. Ideal para rascunhos.",
    )
    effective_dpi = min(dpi, FAST_MODE_MAX_DPI) if fast_mode else dpi
    tess_config = get_tesseract_config(fast_mode)
    if fast_mode and effective_dpi != dpi:
        st.sidebar.caption(f"DPI limitado a {effective_dpi} no modo rapido.")

    try:
        uploaded_file = st.file_uploader(
            "Envie um PDF ou imagem para extrair texto",
            type=["pdf", "png", "jpg", "jpeg", "tiff", "bmp"],
        )

        if not uploaded_file:
            st.info("Envie um arquivo para comecar.")
            return

        file_suffix = uploaded_file.name.split(".")[-1].lower()

        if file_suffix == "pdf":
            file_bytes = load_pdf_bytes(uploaded_file)
            try:
                page_count = get_pdf_page_count(file_bytes)
            except Exception as exc:
                st.error(f"Falha ao ler PDF: {exc}")
                return

            st.write(f"PDF detectado com **{page_count}** paginas.")
            selected_pages = build_page_selection(page_count)
            if not selected_pages:
                st.warning("Selecione pelo menos uma pagina para processar.")
                return

            selected_pages_sorted = sorted(set(selected_pages))
            if selected_pages_sorted != selected_pages:
                st.info("Paginas reordenadas para seguir a sequencia original do PDF.")

            cols = st.columns(2)
            with cols[0]:
                run_ocr = st.button("Executar OCR nas paginas selecionadas", type="primary", key="btn_visual_ocr")
            with cols[1]:
                download_ocr_pdf = st.button(
                    "Baixar PDF com OCR das paginas selecionadas",
                    type="primary",
                    key="btn_download_ocr",
                )

            if download_ocr_pdf:
                try:
                    with st.spinner("Gerando PDF com OCR..."):
                        searchable_pdf = build_searchable_pdf(
                            file_bytes,
                            selected_pages_sorted,
                            effective_dpi,
                            lang,
                            tess_config,
                        )
                except pytesseract.TesseractNotFoundError:
                    st.error("Tesseract nao encontrado. Ajuste o caminho na barra lateral ou instale o Tesseract.")
                    return
                except pytesseract.TesseractError as exc:
                    st.error(f"Erro do Tesseract ao gerar PDF: {exc}")
                    return
                except Exception as exc:
                    st.error(f"Falha ao gerar PDF com OCR. Verifique se o Poppler esta instalado. Erro: {exc}")
                    return

                st.session_state["pending_download"] = {
                    "data": searchable_pdf.getvalue(),
                    "name": f"paginas_ocr_{uploaded_file.name}",
                }

            if run_ocr:
                try:
                    results = ocr_pdf_pages(
                        file_bytes,
                        selected_pages_sorted,
                        effective_dpi,
                        lang,
                        tess_config,
                    )
                except pytesseract.TesseractNotFoundError:
                    st.error("Tesseract nao encontrado. Ajuste o caminho na barra lateral ou instale o Tesseract.")
                    return
                except pytesseract.TesseractError as exc:
                    st.error(f"Erro do Tesseract ao executar OCR: {exc}")
                    return
                except Exception as exc:
                    st.error(f"Falha ao executar o OCR. Verifique se o Poppler esta instalado. Erro: {exc}")
                    return

                if results:
                    display_ocr_output(results)
                else:
                    st.warning("Nenhuma pagina foi processada.")

            pending_download = st.session_state.pop("pending_download", None)
            if pending_download:
                data = pending_download["data"]
                name = pending_download["name"]
                st.success("PDF com OCR gerado. O download iniciara automaticamente.")
                fallback_label = "Se o download nao iniciar, clique aqui"
                st.download_button(
                    fallback_label,
                    data=data,
                    file_name=name,
                    mime="application/pdf",
                    key="manual_download_fallback",
                )
                auto_click_button(fallback_label)

        else:
            try:
                image = Image.open(uploaded_file)
                image = image.convert("L" if fast_mode else "RGB")
            except Exception as exc:
                st.error(f"Nao foi possivel abrir a imagem: {exc}")
                return

            if st.button("Executar OCR na imagem", type="primary"):
                try:
                    text = pytesseract.image_to_string(image, lang=lang, config=tess_config)
                    display_ocr_output([(1, text)])
                except pytesseract.TesseractNotFoundError:
                    st.error("Tesseract nao encontrado. Ajuste o caminho na barra lateral ou instale o Tesseract.")
                except Exception as exc:
                    st.error(f"Erro ao executar o Tesseract: {exc}")
                finally:
                    try:
                        image.close()
                    except Exception:
                        pass
            else:
                try:
                    image.close()
                except Exception:
                    pass

    except Exception as unexpected:
        st.error("Erro inesperado ao processar o arquivo.")
        st.exception(unexpected)


if __name__ == "__main__":
    main()
