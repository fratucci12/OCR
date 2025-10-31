import base64
import io
import json
import os
import shutil
from pathlib import Path
from typing import List

import streamlit as st
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter
from pdf2image import convert_from_bytes
import pytesseract
from streamlit.components.v1 import html


def rerun_app() -> None:
    """Trigger a Streamlit rerun, compatible with new and old APIs."""
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if not rerun:
        raise RuntimeError("Streamlit nao expoe API para reiniciar a execucao.")
    rerun()


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


def ocr_images(images: List[Image.Image], lang: str) -> List[str]:
    texts = []
    for idx, image in enumerate(images, start=1):
        with st.spinner(f"Executando OCR na pagina {idx}..."):
            text = pytesseract.image_to_string(image, lang=lang)
        texts.append(text)
    return texts


def convert_pdf_pages_to_images(file_bytes: bytes, pages: List[int], dpi: int) -> List[Image.Image]:
    images: List[Image.Image] = []
    for page_number in pages:
        page_images = convert_from_bytes(
            file_bytes,
            dpi=dpi,
            first_page=page_number,
            last_page=page_number,
        )
        images.extend(page_images)
    return images


def build_searchable_pdf(images: List[Image.Image], lang: str) -> io.BytesIO:
    writer = PdfWriter()
    for idx, image in enumerate(images, start=1):
        with st.spinner(f"Gerando PDF OCR da pagina {idx}..."):
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(image, extension="pdf", lang=lang)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer.add_page(reader.pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def trigger_auto_download(data: bytes, filename: str) -> None:
    """Render HTML component that builds a Blob and forces the download."""
    encoded = base64.b64encode(data).decode("ascii")
    html(
        f"""
        <script>
        const base64Data = {json.dumps(encoded)};
        const byteCharacters = atob(base64Data);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i += 1) {{
            byteNumbers[i] = byteCharacters.charCodeAt(i);
        }}
        const byteArray = new Uint8Array(byteNumbers);
        const blob = new Blob([byteArray], {{ type: "application/pdf" }});
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = {json.dumps(filename)};
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        </script>
        """,
        height=0,
    )


def display_ocr_output(texts: List[str]) -> None:
    for idx, text in enumerate(texts, start=1):
        st.subheader(f"Resultado - Pagina {idx}")
        st.text_area(
            label=f"OCR pagina {idx}",
            value=text,
            height=min(400, max(120, len(text) // 2)),
            key=f"text_page_{idx}",
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
    dpi = st.sidebar.slider("Resolucao (DPI) para conversao de paginas", 150, 400, 300, step=50)

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
                with st.spinner("Preparando paginas selecionadas..."):
                    images = convert_pdf_pages_to_images(file_bytes, selected_pages, dpi)
            except Exception as exc:
                st.error(f"Falha ao converter paginas em imagens. Verifique se o Poppler esta instalado. Erro: {exc}")
                return

            try:
                searchable_pdf = build_searchable_pdf(images, lang=lang)
            except pytesseract.TesseractNotFoundError:
                st.error("Tesseract nao encontrado. Ajuste o caminho na barra lateral ou instale o Tesseract.")
                return
            except Exception as exc:
                st.error(f"Erro ao gerar PDF com OCR: {exc}")
                return

            st.session_state["pending_download_data"] = searchable_pdf.getvalue()
            st.session_state["pending_download_name"] = f"paginas_ocr_{uploaded_file.name}"
            rerun_app()

        if run_ocr:
            try:
                images = convert_pdf_pages_to_images(file_bytes, selected_pages, dpi)
            except Exception as exc:
                st.error(f"Falha ao converter paginas em imagens. Verifique se o Poppler esta instalado. Erro: {exc}")
                return

            try:
                texts = ocr_images(images, lang=lang)
            except pytesseract.TesseractNotFoundError:
                st.error("Tesseract nao encontrado. Ajuste o caminho na barra lateral ou instale o Tesseract.")
                return
            except Exception as exc:
                st.error(f"Erro ao executar o Tesseract: {exc}")
                return

            display_ocr_output(texts)

        pending_data = st.session_state.pop("pending_download_data", None)
        pending_name = st.session_state.pop("pending_download_name", None)
        if pending_data and pending_name:
            trigger_auto_download(pending_data, pending_name)
            st.success("PDF com OCR gerado. O download iniciara automaticamente.")
            st.download_button(
                "Se o download nao iniciar, clique aqui",
                data=pending_data,
                file_name=pending_name,
                mime="application/pdf",
                key="manual_download_fallback",
            )

    else:
        try:
            image = Image.open(uploaded_file).convert("RGB")
        except Exception as exc:
            st.error(f"Nao foi possivel abrir a imagem: {exc}")
            return

        if st.button("Executar OCR na imagem", type="primary"):
            try:
                text = pytesseract.image_to_string(image, lang=lang)
                display_ocr_output([text])
            except pytesseract.TesseractNotFoundError:
                st.error("Tesseract nao encontrado. Ajuste o caminho na barra lateral ou instale o Tesseract.")
            except Exception as exc:
                st.error(f"Erro ao executar o Tesseract: {exc}")


if __name__ == "__main__":
    main()
