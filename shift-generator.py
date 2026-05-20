"""
## Constraints:
- Initially, each volunteer is assigned one build-up shift if available
- Initially, each volunteer is assigned two festival shifts if available
- Initially, each volunteer is assigned one tear-down shift if available

- Festival shifts:
    - Volunteers are assigned to festival shifts based on their preferences, if possible
    - Both shifts should be a contiguous block of the same type if possible (e.g. Schankwagen 1 and Schankewagen 2, not Schankwagen 1 and Cocktailbar)

## Input data:
Festival shifts are read from a json file with the following format:
[
    {
        "shift_type": "Schankwagen",
        "first_shift_start": "2026-06-05T13:00:00+02:00",
        "shift_duration": 2,
        "locations": ["Wagen 1", "Wagen 2"],
        "number_of_shifts": [5, 5],
        "people_per_shift": [6, 4]
    },
    ...
]
meaning that there are 5 shifts of type "Schankwagen" at "Wagen 1" with 6 people per shift and 5 shifts of type "Schankwagen" at "Wagen 2" with 4 people per shift. The first shift starts on June 5th, 2026 at 13:00 and each shift lasts for 2 hours. The second shift starts immediately after the first one ends (i.e. at 15:00).

Volunteer data is read from a csv file with the format
"Benutzer-ID","Anzeigename des Nutzers","Zeitstempel","Vorname","Nachname","E-Mail-Adresse unter der du zuverlässig erreichbar bist.","Telefonnummer - Wir planen eine WhatsApp/Signal-Gruppe für das Team zu erstellen.","Aufbau / Abbau","Hast du vielleicht schon besondere Skills, die uns ganz besonders helfen könnten?","Gibt es einen Bereich in dem du besonders gerne eingesetzt werden möchtest? Wähle gerne mehrere aus, wir können leider noch keine Zuteilung garantieren.","T-Shirt Größe","Gibt es sonst noch etwas, was wir beachten oder wissen sollten über dich?"
where multiple options in the "Aufbau / Abbau" and "Gibt es einen Bereich..." columns are separated by a semi-colon and a space ("; ").

## Output:
Generate master shift schedule as csv file. Uses -s --shifts for shifts json file and -c --csv for volunteer csv file. Format of output csv file:
"start_time","end_time","shift_type","location","first_name","last_name","email","phone"
Separate script: Generate calendar files and manifest containing email addresses for ics files in output directory. Uses -f --file to specify master schedule file.  
Separate script: Send calendar invites to volunteers. Uses -d --directory to specify directory containing ics files and manifest.

## Approach:
1. Read volunteer data and shift data from input files
2. Create a list of all available shifts based on the shift data
3. Generate bitmap of volunteer availability for each shift based on their preferences and availability
4. Assign volunteers to shifts based on their availability and preferences, ensuring that the constraints are met. Select randomly among volunteers with the same availability and preferences to ensure fairness.
5. Write the master shift schedule, build-up schedule and tear-down schedule to three separate output csv files
"""

from datetime import datetime, timedelta, date, time
from pathlib import Path
from random import shuffle

import argparse
import csv
import dateutil.parser
import json
import logging
import re


COL_ID = "Benutzer-ID"
COL_FIRST_NAME = "Vorname"
COL_LAST_NAME = "Nachname"
COL_EMAIL = "E-Mail-Adresse unter der du zuverlässig erreichbar bist."
COL_PHONE = "Telefonnummer - Wir planen eine WhatsApp/Signal-Gruppe für das Team zu erstellen."
COL_AVAILABILITY = "Aufbau / Abbau"
COL_TASK_PREFERENCE = "Gibt es einen Bereich in dem du besonders gerne eingesetzt werden möchtest? Wähle gerne mehrere aus, wir können leider noch keine Zuteilung garantieren."
AVAILABILITY_YEAR = 2026
MORNING_START = time(8, 0)
MORNING_END = time(12, 0)
AFTERNOON_START = time(12, 0)
AFTERNOON_END = time(16, 0)

OPTIONS_AVAILABILITY_PRE = ["Di, 02.06. vormittags/ before noon", "Di, 02.06. nachmittags/ afternoon",
                            "Mi, 04.06. vormittags/ before noon", "Mi, 04.06. nachmittags/ afternoon",
                            "Do, 05.06. vormittags/ before noon", "Do, 05.06. nachmittags/ afternoon"]
OPTIONS_AVAILABILITY_FESTIVAL = ["Fr, 06.06. vormittags/ before noon", "Fr, 06.06. nachmittags/ afternoon"]
OPTIONS_AVAILABILITY_POST = ["Sa, 07.06. vormittags/ before noon", "Sa, 07.06. nachmittags/ afternoon",
                             "So, 08.06. vormittags/ before noon", "So, 08.06. nachmittags/ afternoon"]
OPTIONS_TASK_PREFERENCE = ["mir egal :) - no preferences",
                           "Schankwagen - Bar",
                           "Cocktailbar",
                           "Aufbau - Set-Up",
                           "Veranstaltungstechnik - Event Technology",
                           "Merchverkauf - Merch sales"]
                           # "Other:.*": Ignored

TASK_MASK = {
    "Aufbau - Set-Up": 0b001,
    "Abbau - Tear-Down": 0b010,
    "Schankwagen - Bar": 0b100,
    "Cocktailbar": 0b1000,
    "Veranstaltungstechnik - Event Technology": 0b10000,
    "Merchverkauf - Merch sales": 0b100000,
}
FESTIVAL_TASK_MASK = (
    TASK_MASK["Schankwagen - Bar"] |
    TASK_MASK["Cocktailbar"] |
    TASK_MASK["Veranstaltungstechnik - Event Technology"] |
    TASK_MASK["Merchverkauf - Merch sales"]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign shifts automatically")

    parser.add_argument(
        "-s", "--shifts",
        type=Path,
        help="Path to shifts json file")
    parser.add_argument(
        "-c", "--csv",
        type=Path,
        help="Path to volunteer csv file")

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output")
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress all output")
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug output")
    
    return parser.parse_args()

def setup_logging(args: argparse.Namespace) -> None:
    if args.debug:
        logging_level = logging.DEBUG
    elif args.verbose:
        logging_level = logging.INFO
    elif args.quiet:
        logging_level = logging.ERROR
    else:
        logging_level = logging.WARNING

    logging.basicConfig(level=logging_level, format="%(asctime)s - %(levelname)s - %(message)s")

def initialise_shifts(input_json: Path) -> dict:
    """
    Generate a dict that contains shift types as keys.
    The value is a dict containing the time slots as keys.
    The value is a location:#vacancies dict.
    """
    with input_json.open() as f:
        abstract_shifts = json.load(f)

    shifts = {}
    for shift in abstract_shifts:
        shift_type = shift["shift_type"]
        first_shift_start = dateutil.parser.isoparse(shift["first_shift_start"])
        shift_duration = timedelta(hours=shift["shift_duration"])
        locations = shift["locations"]
        number_of_shifts = shift["number_of_shifts"]
        people_per_shift = shift["people_per_shift"]

        if shift_type not in shifts:
            shifts[shift_type] = {}

        for i, location in enumerate(locations):
            for j in range(number_of_shifts[i]):
                start_time = first_shift_start + j * shift_duration
                end_time = start_time + shift_duration
                time_slot = (start_time, end_time)

                if time_slot not in shifts[shift_type]:
                    shifts[shift_type][time_slot] = {}

                shifts[shift_type][time_slot][location] = people_per_shift[i]

    return shifts

def split_availability(availability: list) -> tuple:
    """
    Split availability into three categories: build-up, festival and teardown.
    """
    availability_pre = [slot for slot in availability if slot in OPTIONS_AVAILABILITY_PRE]
    availability_festival = [slot for slot in availability if slot in OPTIONS_AVAILABILITY_FESTIVAL]
    availability_post = [slot for slot in availability if slot in OPTIONS_AVAILABILITY_POST]

    return (availability_pre, availability_festival, availability_post)

def normalize_header(value: str) -> str:
    return value.replace("\u00a0", " ").strip()

def parse_availability_option(option: str) -> tuple[date, bool] | None:
    match = re.search(r"(\d{2})\.(\d{2})\.", option)
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    option_lower = option.lower()
    if "vormittag" in option_lower:
        is_morning = True
    elif "nachmittag" in option_lower:
        is_morning = False
    else:
        return None

    return (date(AVAILABILITY_YEAR, month, day), is_morning)

def normalize_availability(availability: list) -> list:
    normalized = []
    for option in availability:
        parsed = parse_availability_option(option)
        if parsed:
            normalized.append(parsed)
    return normalized

def availability_to_time_slot(availability_slot: tuple[date, bool]) -> tuple:
    slot_date, is_morning = availability_slot
    if is_morning:
        return (
            datetime.combine(slot_date, MORNING_START),
            datetime.combine(slot_date, MORNING_END)
        )
    return (
        datetime.combine(slot_date, AFTERNOON_START),
        datetime.combine(slot_date, AFTERNOON_END)
    )

def read_volunteers(input_csv: Path) -> dict:
    """
    Generate a dict that contains volunteer IDs as keys and their data (name, task bitmask, email, phone) as values.
    """
    volunteers = {}
    with input_csv.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        header_map = {normalize_header(name): idx for idx, name in enumerate(header)}

        def read_field(row: list, column_name: str) -> str:
            index = header_map.get(normalize_header(column_name))
            if index is None or index >= len(row):
                return ""
            return row[index].strip('"')

        for row in reader:
            volunteer_id = read_field(row, COL_ID)
            first_name = read_field(row, COL_FIRST_NAME)
            last_name = read_field(row, COL_LAST_NAME)
            email = read_field(row, COL_EMAIL)
            phone = read_field(row, COL_PHONE)

            availability_value = read_field(row, COL_AVAILABILITY)
            availability_raw = availability_value.split("; ") if availability_value else []
            availability = split_availability(availability_raw)

            task_value = read_field(row, COL_TASK_PREFERENCE)
            task_preference = task_value.split("; ") if task_value else []

            task_bitmask = 0
            for task in task_preference:
                if task in TASK_MASK:
                    task_bitmask |= TASK_MASK[task]

            volunteers[volunteer_id] = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "availability_buildup": normalize_availability(availability[0]),
                "availability_festival": normalize_availability(availability[1]),
                "availability_teardown": normalize_availability(availability[2]),
                "task_bitmask": task_bitmask
            }

    return volunteers

def match_time_to_slot(time_slot: tuple, availability: list) -> bool:
    start = time_slot[0]
    is_morning = start.time() < time(12, 0)
    slot_date = start.date()
    return any(
        available_date == slot_date and available_is_morning == is_morning
        for available_date, available_is_morning in availability
    )

def festival_mask_for_shift_type(shift_type: str) -> int:
    shift_type_lower = shift_type.lower()
    for task_name, mask in TASK_MASK.items():
        if task_name in ("Aufbau - Set-Up", "Abbau - Tear-Down"):
            continue
        if shift_type_lower in task_name.lower():
            return mask
    return 0

def assign_shifts(volunteers: dict, shifts: dict) -> dict:
    """
    Assign volunteers to shifts based on their availability and preferences.
    Returns a dict with one of "buildup", "festival" and "teardown" as keys and a list of assigned shifts as values.
    Each shift is a dict containing the shift type, location, time slot and assigned volunteer IDs.
    """
    assigned_shifts = {"buildup": [], "festival": [], "teardown": []}
    shift_slots = []
    for shift_type, shift_time_slots in shifts.items():
        for time_slot, locations in shift_time_slots.items():
            for location in locations:
                shift_slots.append((shift_type, time_slot, location))

    for volunteer_id, volunteer_data in volunteers.items():
        assigned_time_slots = set()
        shift_slots_local = shift_slots[:]
        shuffle(shift_slots_local)

        consecutive_festival_pairs = []
        for shift_type, shift_time_slots in shifts.items():
            time_slots_sorted = sorted(shift_time_slots.keys(), key=lambda slot: slot[0])
            for first_slot, second_slot in zip(time_slots_sorted, time_slots_sorted[1:]):
                if first_slot[1] == second_slot[0]:
                    consecutive_festival_pairs.append((shift_type, first_slot, second_slot))

        shuffle(consecutive_festival_pairs)

        def assign_for_category(category: str, availability: list, required_mask: int, max_shifts: int) -> None:
            if max_shifts <= 0 or not availability:
                return
            if required_mask and not (volunteer_data["task_bitmask"] & required_mask):
                return

            if category in ("buildup", "teardown"):
                availability_local = availability[:]
                shuffle(availability_local)
                for availability_slot in availability_local:
                    time_slot = availability_to_time_slot(availability_slot)
                    if time_slot in assigned_time_slots:
                        continue
                    assigned_shifts[category].append({
                        "shift_type": "Aufbau - Set-Up" if category == "buildup" else "Abbau - Tear-Down",
                        "location": "",
                        "time_slot": time_slot,
                        "volunteer_id": volunteer_id
                    })
                    assigned_time_slots.add(time_slot)
                    return
                return

            def pick_location(shift_type: str, time_slot: tuple) -> str | None:
                for location, vacancies in shifts[shift_type][time_slot].items():
                    if vacancies > 0:
                        return location
                return None

            if category == "festival" and max_shifts >= 2:
                for shift_type, first_slot, second_slot in consecutive_festival_pairs:
                    if first_slot in assigned_time_slots or second_slot in assigned_time_slots:
                        continue
                    required_task_mask = festival_mask_for_shift_type(shift_type)
                    if required_task_mask and not (volunteer_data["task_bitmask"] & required_task_mask):
                        continue
                    if not match_time_to_slot(first_slot, availability):
                        continue
                    if not match_time_to_slot(second_slot, availability):
                        continue

                    first_location = pick_location(shift_type, first_slot)
                    if not first_location:
                        continue
                    second_location = pick_location(shift_type, second_slot)
                    if not second_location:
                        continue

                    assigned_shifts[category].append({
                        "shift_type": shift_type,
                        "location": first_location,
                        "time_slot": first_slot,
                        "volunteer_id": volunteer_id
                    })
                    shifts[shift_type][first_slot][first_location] -= 1
                    assigned_time_slots.add(first_slot)

                    assigned_shifts[category].append({
                        "shift_type": shift_type,
                        "location": second_location,
                        "time_slot": second_slot,
                        "volunteer_id": volunteer_id
                    })
                    shifts[shift_type][second_slot][second_location] -= 1
                    assigned_time_slots.add(second_slot)
                    return

            assigned_count = 0
            for shift_type, time_slot, location in shift_slots_local:
                if assigned_count >= max_shifts:
                    break
                if time_slot in assigned_time_slots:
                    continue
                required_task_mask = festival_mask_for_shift_type(shift_type)
                if required_task_mask and not (volunteer_data["task_bitmask"] & required_task_mask):
                    continue
                if not match_time_to_slot(time_slot, availability):
                    continue
                if shifts[shift_type][time_slot][location] <= 0:
                    continue

                assigned_shifts[category].append({
                    "shift_type": shift_type,
                    "location": location,
                    "time_slot": time_slot,
                    "volunteer_id": volunteer_id
                })
                shifts[shift_type][time_slot][location] -= 1
                assigned_time_slots.add(time_slot)
                assigned_count += 1

        assign_for_category("buildup", volunteer_data["availability_buildup"], TASK_MASK["Aufbau - Set-Up"], 1)
        assign_for_category("festival", volunteer_data["availability_festival"], FESTIVAL_TASK_MASK, 2)
        assign_for_category("teardown", volunteer_data["availability_teardown"], TASK_MASK["Abbau - Tear-Down"], 1)

    return assigned_shifts

def write_master_schedule(shifts: dict, volunteers: dict, output_dir: Path) -> None:
    buildup_path = output_dir / "buildup_schedule.csv"
    festival_path = output_dir / "festival_schedule.csv"
    teardown_path = output_dir / "teardown_schedule.csv"
    pass

def main() -> int:
    args = parse_args()
    setup_logging(args)

    shifts_path = args.shifts or Path("Input/shifts.json")
    volunteers_path = args.csv or Path("Input/Deine Hilfe auf dem Campusfestival Kiel am 05.06.2026 - Your help at the Kiel Campus Festival on 05.06.2026 (Ergebnisse).csv")
    shifts_available = initialise_shifts(shifts_path)
    print(shifts_available)
    volunteers = read_volunteers(volunteers_path)

    shifts = assign_shifts(volunteers, shifts_available)
    write_master_schedule(shifts, Path("Output"))

    return 0

if __name__ == "__main__":
    exit(main())