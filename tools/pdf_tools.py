from pypdf import PdfReader

def extract_text_from_pdf(pdf_path):
    """ Extracts  raw text from a PDF file. """
    reader = PdfReader(pdf_path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()