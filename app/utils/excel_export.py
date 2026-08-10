from io import BytesIO
from openpyxl import Workbook

def make_xlsx(headers: list[str], rows: list[list[str]]) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio
