import argparse
import csv
import logging
import re
from datetime import datetime
from icalendar import Calendar, Event, vText
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ICS calendar files from master shift schedule.")

    parser.add_argument(
        "-f", "--file",
        type=Path,
        required=True,
        help="Path to master schedule csv file"
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("Output"),
        help="Directory for output ics files and manifest"
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


def read_master_schedule(file_path: Path) -> dict:
    volunteers = {}
    try:
        with file_path.open('r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                email = row.get('email', '').strip()
                if not email:
                    continue
                if email not in volunteers:
                    volunteers[email] = {
                        'first_name': row.get('first_name', ''),
                        'last_name': row.get('last_name', ''),
                        'shifts': []
                    }
                volunteers[email]['shifts'].append(row)
    except Exception as e:
        logging.error(f"Failed to read master schedule: {e}")
        
    return volunteers


def generate_ics_files(volunteers: dict, output_dir: Path) -> list[tuple[Path, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries = []
    
    for email, data in volunteers.items():
        cal = Calendar()
        cal.add('prodid', '-//Campusfestival Shifts//')
        cal.add('version', '2.0')
        
        for shift in data['shifts']:
            event = Event()
            summary = f"Campusfestival: {shift.get('shift_type', 'Shift')}"
            event.add('summary', vText(summary))
            
            try:
                start_dt = datetime.fromisoformat(shift['start_time'])
                end_dt = datetime.fromisoformat(shift['end_time'])
                event.add('dtstart', start_dt)
                event.add('dtend', end_dt)
            except ValueError as e:
                logging.warning(f"Invalid date format for {email} shift: {e}")
                continue
                
            location = shift.get('location', '')
            if location:
                event.add('location', vText(location))
                
            cal.add_component(event)
            
        # Create a safe file name based on the email address
        safe_email = re.sub(r'[^a-zA-Z0-9]', '_', email)
        filename = f"{safe_email}.ics"
        ics_path = output_dir / filename
        
        try:
            with ics_path.open('wb') as f:
                f.write(cal.to_ical())
            manifest_entries.append((ics_path.absolute(), email))
        except Exception as e:
            logging.error(f"Failed to write ICS file for {email}: {e}")
            
    return manifest_entries


def write_manifest(manifest_entries: list[tuple[Path, str]], output_dir: Path) -> None:
    manifest_path = output_dir / 'manifest.txt'
    try:
        with manifest_path.open('w', encoding='utf-8') as f:
            for ics_path, email in manifest_entries:
                f.write(f"{ics_path},{email}\n")
        logging.info(f"Manifest written to {manifest_path}")
    except Exception as e:
        logging.error(f"Failed to write manifest: {e}")


def main() -> int:
    args = parse_args()
    setup_logging(args)

    if not args.file.exists():
        logging.error(f"Master schedule file not found: {args.file}")
        return 1

    logging.info(f"Reading master schedule from {args.file}")
    volunteers = read_master_schedule(args.file)
    
    if not volunteers:
        logging.warning("No volunteers found in the master schedule.")
        return 0

    logging.info(f"Generating ICS files for {len(volunteers)} volunteers...")
    manifest_entries = generate_ics_files(volunteers, args.output)
    
    write_manifest(manifest_entries, args.output)
    logging.info("ICS generation complete.")

    return 0


if __name__ == "__main__":
    exit(main())