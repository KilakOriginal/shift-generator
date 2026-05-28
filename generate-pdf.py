import argparse
import csv
import logging
import re
import unicodedata

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate PDF overview of volunteer schedules by shift type and location."
    )

    parser.add_argument(
        "-f", "--file",
        type=Path,
        help="Path to master schedule csv file"
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Directory for output PDF file"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress all output",
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug output",
    )

    return parser.parse_args()

def setup_logging(args: argparse.Namespace) -> None:
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
    elif args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    elif args.quiet:
        logging.basicConfig(level=logging.CRITICAL, format="%(asctime)s - %(levelname)s - %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")


def read_master_schedule(file_path: Path) -> list[dict]:
    rows = []
    try:
        with file_path.open('r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception as e:
        logging.error(f"Failed to read master schedule: {e}")
        
    return rows

def slugify(value, allow_unicode=False):
    """
    Taken from https://github.com/django/django/blob/master/django/utils/text.py
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')

def format_time_value(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M")

def write_pdf_overview(schedule_rows: list[dict], output_dir: Path) -> None:
    """
    Generates a PDF overview with one page per shift type and location.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    schedules = {}
    for row in schedule_rows:
        shift_type = row.get("shift_type") or "Shift"
        location = row.get("location") or "Unknown Location"
        key = (shift_type, location)
        if key not in schedules:
            schedules[key] = []
        schedules[key].append({
            "start_time": row.get("start_time", ""),
            "end_time": row.get("end_time", ""),
            "first_name": row.get("first_name", ""),
            "last_name": row.get("last_name", ""),
            "phone": row.get("phone", ""),
        })

    if not schedules:
        logging.warning("No schedule rows found; skipping PDF generation.")
        return

    styles = getSampleStyleSheet()
    pdf_path = output_dir / "shift_type_location_schedules.pdf"
    try:
        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=landscape(A4),
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
            title="Shift type and location schedules",
        )

        story = []
        sorted_items = sorted(schedules.items(), key=lambda item: (item[0][0], item[0][1]))
        for index, ((shift_type, location), shifts) in enumerate(sorted_items):
            if index > 0:
                story.append(PageBreak())
            title = Paragraph(f"{shift_type} - {location}", styles["Title"])
            table_data = [["Start", "End", "First name", "Last name", "Phone"]]
            for shift in sorted(shifts, key=lambda entry: (entry["start_time"], entry["end_time"])):
                table_data.append([
                    format_time_value(shift["start_time"]),
                    format_time_value(shift["end_time"]),
                    shift["first_name"],
                    shift["last_name"],
                    shift["phone"],
                ])

            table = Table(table_data, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f2f2f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c0c0c0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f2f2f2")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))

            story.extend([title, Spacer(1, 12), table])

        doc.build(story)
        logging.info(f"Generated PDF overview at {pdf_path}")
    except Exception as e:
        logging.error(f"Failed to write PDF overview: {e}")

def main() -> int:
    args = parse_args()
    setup_logging(args)

    master_schedule = args.file or Path(__file__).resolve().parent / "Output/master_schedule.csv"
    output_dir = args.output or Path(__file__).resolve().parent / "Output/"

    schedule_rows = read_master_schedule(master_schedule)
    write_pdf_overview(schedule_rows, output_dir)

    return 0

if __name__ == "__main__":
    exit(main())