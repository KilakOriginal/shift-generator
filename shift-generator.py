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
AFTERNOON_END = time(23, 59, 59)

MAX_SHIFTS_BUILDUP = 1
MAX_SHIFTS_FESTIVAL = 2
MAX_SHIFTS_TEARDOWN = 1

OPTIONS_AVAILABILITY_PRE = ["Di, 02.06. vormittags/ before noon", "Di, 02.06. nachmittags/ afternoon",
                            "Mi, 03.06. vormittags/ before noon", "Mi, 03.06. nachmittags/ afternoon",
                            "Do, 04.06. vormittags/ before noon", "Do, 04.06. nachmittags/ afternoon"]
OPTIONS_AVAILABILITY_FESTIVAL = ["Fr, 05.06. vormittags/ before noon", "Fr, 05.06. nachmittags/ afternoon"]
OPTIONS_AVAILABILITY_POST = ["Sa, 06.06. vormittags/ before noon", "Sa, 06.06. nachmittags/ afternoon",
                             "So, 07.06. vormittags/ before noon", "So, 07.06. nachmittags/ afternoon"]
OPTIONS_TASK_PREFERENCE = ["mir egal :) - no preferences",
                           "Schankwagen - Bar",
                           "Cocktailbar",
                           "Veranstaltungstechnik - Event Technology",
                           "Merchverkauf - Merch sales"]
                           # "Other:.*": Ignored

TASK_MASK = {
    "Schankwagen - Bar": 0b100,
    "Cocktailbar": 0b1000,
    "Veranstaltungstechnik - Event Technology": 0b10000,
    "Merchverkauf - Merch sales": 0b100000,
}
NO_PREFERENCE_MASK = 0b1000000
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
        "-o", "--output",
        type=Path,
        help="Directory for output csv files")

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
        first_shift_start_value = shift["first_shift_start"]
        if isinstance(first_shift_start_value, list):
            if not first_shift_start_value:
                raise ValueError("first_shift_start list must not be empty")
            start_times = [dateutil.parser.isoparse(value) for value in first_shift_start_value]
        else:
            start_times = [dateutil.parser.isoparse(first_shift_start_value)]
        shift_duration_value = shift["shift_duration"]
        locations = shift["locations"]
        number_of_shifts = shift["number_of_shifts"]
        people_value = shift["people_per_shift"]

        if shift_type not in shifts:
            shifts[shift_type] = {}

        if not number_of_shifts:
            continue

        if len(start_times) > 1 and len(start_times) != len(locations):
            raise ValueError("first_shift_start list must match locations")
        if len(start_times) == 1:
            start_times = start_times * len(locations)

        duration_mode = "per_shift"
        duration_hours = None
        duration_by_location = None
        if isinstance(shift_duration_value, list):
            if not shift_duration_value:
                raise ValueError("shift_duration list must not be empty")
            if any(isinstance(item, list) for item in shift_duration_value):
                if not all(isinstance(item, list) for item in shift_duration_value):
                    raise ValueError("shift_duration list must be all floats or all lists")
                if len(shift_duration_value) != len(locations):
                    raise ValueError("shift_duration list must match locations")
                duration_mode = "per_location"
                duration_by_location = []
                for durations in shift_duration_value:
                    if not durations:
                        raise ValueError("shift_duration per-location list must not be empty")
                    duration_by_location.append([float(value) for value in durations])
            else:
                duration_hours = [float(value) for value in shift_duration_value]
        else:
            duration_hours = [float(shift_duration_value)]

        people_mode = "per_location"
        people_by_location = None
        if isinstance(people_value, list):
            if not people_value:
                raise ValueError("people_per_shift list must not be empty")
            if any(isinstance(item, list) for item in people_value):
                if not all(isinstance(item, list) for item in people_value):
                    raise ValueError("people_per_shift list must be all ints or all lists")
                if len(people_value) != len(locations):
                    raise ValueError("people_per_shift list must match locations")
                people_mode = "per_location_per_shift"
                people_by_location = []
                for counts in people_value:
                    if not counts:
                        raise ValueError("people_per_shift per-location list must not be empty")
                    people_by_location.append([int(value) for value in counts])
            else:
                if len(people_value) != len(locations):
                    raise ValueError("people_per_shift list must match locations")
                people_by_location = [int(value) for value in people_value]
        else:
            people_by_location = [int(people_value) for _ in locations]

        max_shifts = max(number_of_shifts)
        if duration_mode == "per_location":
            for i, durations in enumerate(duration_by_location):
                if i >= len(number_of_shifts):
                    raise ValueError("number_of_shifts must match locations")
                if len(durations) > 1 and len(durations) < number_of_shifts[i]:
                    raise ValueError("shift_duration list must match or exceed number_of_shifts for each location")
        else:
            if len(duration_hours) > 1 and len(duration_hours) < max_shifts:
                raise ValueError("shift_duration list must match or exceed number_of_shifts")

        if people_mode == "per_location_per_shift":
            for i, counts in enumerate(people_by_location):
                if i >= len(number_of_shifts):
                    raise ValueError("number_of_shifts must match locations")
                if len(counts) > 1 and len(counts) < number_of_shifts[i]:
                    raise ValueError("people_per_shift list must match or exceed number_of_shifts for each location")

        time_slots = []
        if duration_mode == "per_shift":
            shared_start = len(set(start_times)) == 1
            if shared_start:
                base_start = start_times[0]
                if len(duration_hours) == 1:
                    shift_duration = timedelta(hours=duration_hours[0])
                    for j in range(max_shifts):
                        start_time = base_start + j * shift_duration
                        end_time = start_time + shift_duration
                        time_slots.append((start_time, end_time))
                else:
                    start_time = base_start
                    for j in range(max_shifts):
                        shift_duration = timedelta(hours=duration_hours[j])
                        end_time = start_time + shift_duration
                        time_slots.append((start_time, end_time))
                        start_time = end_time

        for i, location in enumerate(locations):
            if i >= len(number_of_shifts):
                raise ValueError("number_of_shifts must match locations")
            base_start = start_times[i]
            if duration_mode == "per_location":
                durations = duration_by_location[i]
                time_slots_local = []
                if len(durations) == 1:
                    shift_duration = timedelta(hours=durations[0])
                    for j in range(number_of_shifts[i]):
                        start_time = base_start + j * shift_duration
                        end_time = start_time + shift_duration
                        time_slots_local.append((start_time, end_time))
                else:
                    start_time = base_start
                    for j in range(number_of_shifts[i]):
                        shift_duration = timedelta(hours=durations[j])
                        end_time = start_time + shift_duration
                        time_slots_local.append((start_time, end_time))
                        start_time = end_time
            else:
                if time_slots:
                    time_slots_local = time_slots
                else:
                    time_slots_local = []
                    if len(duration_hours) == 1:
                        shift_duration = timedelta(hours=duration_hours[0])
                        for j in range(number_of_shifts[i]):
                            start_time = base_start + j * shift_duration
                            end_time = start_time + shift_duration
                            time_slots_local.append((start_time, end_time))
                    else:
                        start_time = base_start
                        for j in range(number_of_shifts[i]):
                            shift_duration = timedelta(hours=duration_hours[j])
                            end_time = start_time + shift_duration
                            time_slots_local.append((start_time, end_time))
                            start_time = end_time

            for j in range(number_of_shifts[i]):
                time_slot = time_slots_local[j]
                if people_mode == "per_location_per_shift":
                    counts = people_by_location[i]
                    people_needed = counts[0] if len(counts) == 1 else counts[j]
                else:
                    people_needed = people_by_location[i]

                if time_slot not in shifts[shift_type]:
                    shifts[shift_type][time_slot] = {}

                shifts[shift_type][time_slot][location] = people_needed

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

def availability_dates(options: list) -> set[date]:
    dates = set()
    for option in options:
        parsed = parse_availability_option(option)
        if parsed:
            dates.add(parsed[0])
    return dates

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

        for line_number, row in enumerate(reader, start=2):
            volunteer_id = read_field(row, COL_ID)
            if not volunteer_id:
                volunteer_id = str(line_number)
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
            if not task_preference or "mir egal :) - no preferences" in task_preference:
                task_bitmask = FESTIVAL_TASK_MASK | NO_PREFERENCE_MASK

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
        if shift_type_lower in task_name.lower():
            return mask
    return NO_PREFERENCE_MASK

def assign_shifts(volunteers: dict, shifts: dict) -> dict:
    """
    Assign volunteers to shifts based on their availability and preferences.
    Returns a dict with one of "buildup", "festival" and "teardown" as keys and a list of assigned shifts as values.
    Each shift is a dict containing the shift type, location, time slot and assigned volunteer IDs.
    """
    assigned_shifts = {"buildup": [], "festival": [], "teardown": []}
    availability_dates_pre = availability_dates(OPTIONS_AVAILABILITY_PRE)
    availability_dates_festival = availability_dates(OPTIONS_AVAILABILITY_FESTIVAL)
    availability_dates_post = availability_dates(OPTIONS_AVAILABILITY_POST)

    def category_for_time_slot(time_slot: tuple) -> str | None:
        slot_date = time_slot[0].date()
        if slot_date in availability_dates_festival:
            return "festival"
        if slot_date in availability_dates_pre:
            return "buildup"
        if slot_date in availability_dates_post:
            return "teardown"
        return None

    shift_slots_by_category = {"buildup": [], "festival": [], "teardown": []}
    for shift_type, shift_time_slots in shifts.items():
        for time_slot, locations in shift_time_slots.items():
            category = category_for_time_slot(time_slot)
            if not category:
                continue
            for location in locations:
                shift_slots_by_category[category].append((shift_type, time_slot, location))

    assigned_time_slots = {volunteer_id: set() for volunteer_id in volunteers}
    assigned_counts = {
        volunteer_id: {"buildup": 0, "festival": 0, "teardown": 0}
        for volunteer_id in volunteers
    }

    def max_shifts_for_category(category: str) -> int:
        if category == "buildup":
            return MAX_SHIFTS_BUILDUP
        if category == "festival":
            return MAX_SHIFTS_FESTIVAL
        return MAX_SHIFTS_TEARDOWN

    def availability_for_category(volunteer_data: dict, category: str) -> list:
        if category == "buildup":
            return volunteer_data["availability_buildup"]
        if category == "festival":
            return volunteer_data["availability_festival"]
        return volunteer_data["availability_teardown"]

    def is_eligible(
        volunteer_id: str,
        volunteer_data: dict,
        category: str,
        shift_type: str,
        time_slot: tuple,
        require_preferences: bool,
    ) -> bool:
        if assigned_counts[volunteer_id][category] >= max_shifts_for_category(category):
            return False
        if time_slot in assigned_time_slots[volunteer_id]:
            return False
        availability = availability_for_category(volunteer_data, category)
        if not match_time_to_slot(time_slot, availability):
            return False
        if category == "festival" and require_preferences:
            required_task_mask = festival_mask_for_shift_type(shift_type)
            if required_task_mask and not (volunteer_data["task_bitmask"] & required_task_mask):
                return False
        return True

    def fill_category(category: str, require_preferences: bool) -> None:
        shift_slots = shift_slots_by_category[category]
        if not shift_slots:
            return

        while True:
            candidates = []
            for shift_type, time_slot, location in shift_slots:
                vacancies = shifts[shift_type][time_slot][location]
                if vacancies <= 0:
                    continue
                eligible = [
                    volunteer_id
                    for volunteer_id, volunteer_data in volunteers.items()
                    if is_eligible(
                        volunteer_id,
                        volunteer_data,
                        category,
                        shift_type,
                        time_slot,
                        require_preferences,
                    )
                ]
                if not eligible:
                    continue
                candidates.append((len(eligible), -vacancies, shift_type, time_slot, location, eligible))

            if not candidates:
                break

            candidates.sort(key=lambda item: (item[0], item[1]))
            _, _, shift_type, time_slot, location, eligible = candidates[0]
            min_assigned = min(assigned_counts[volunteer_id][category] for volunteer_id in eligible)
            tied = [
                volunteer_id
                for volunteer_id in eligible
                if assigned_counts[volunteer_id][category] == min_assigned
            ]
            shuffle(tied)
            volunteer_id = tied[0]

            assigned_shifts[category].append({
                "shift_type": shift_type,
                "location": location,
                "time_slot": time_slot,
                "volunteer_id": volunteer_id,
            })
            shifts[shift_type][time_slot][location] -= 1
            assigned_time_slots[volunteer_id].add(time_slot)
            assigned_counts[volunteer_id][category] += 1

    fill_category("buildup", False)
    fill_category("festival", True)
    fill_category("teardown", False)
    # Second pass: fill remaining festival slots without preferences.
    fill_category("festival", False)

    return assigned_shifts

def write_master_schedule(assigned_shifts: dict, volunteers: dict, remaining_shifts: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    master_path = output_dir / "master_schedule.csv"
    buildup_path = output_dir / "buildup_schedule.csv"
    festival_path = output_dir / "festival_schedule.csv"
    teardown_path = output_dir / "teardown_schedule.csv"

    availability_dates_pre = availability_dates(OPTIONS_AVAILABILITY_PRE)
    availability_dates_festival = availability_dates(OPTIONS_AVAILABILITY_FESTIVAL)
    availability_dates_post = availability_dates(OPTIONS_AVAILABILITY_POST)

    def category_for_time_slot(time_slot: tuple) -> str | None:
        slot_date = time_slot[0].date()
        if slot_date in availability_dates_festival:
            return "festival"
        if slot_date in availability_dates_pre:
            return "buildup"
        if slot_date in availability_dates_post:
            return "teardown"
        return None

    def format_time(value: datetime) -> str:
        return value.isoformat()

    def row_for_shift(shift: dict) -> list[str]:
        volunteer = volunteers.get(shift["volunteer_id"], {})
        start_time, end_time = shift["time_slot"]
        return [
            format_time(start_time),
            format_time(end_time),
            shift["shift_type"],
            shift["location"],
            volunteer.get("first_name", ""),
            volunteer.get("last_name", ""),
            volunteer.get("email", ""),
            volunteer.get("phone", ""),
        ]

    def write_schedule(path: Path, schedule: list[dict]) -> None:
        schedule_sorted = sorted(
            schedule,
            key=lambda entry: (
                entry["time_slot"][0].isoformat(),
                entry["shift_type"],
                entry["location"],
            ),
        )
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "start_time",
                "end_time",
                "shift_type",
                "location",
                "first_name",
                "last_name",
                "email",
                "phone",
            ])
            for entry in schedule_sorted:
                writer.writerow(row_for_shift(entry))

    empty_shifts = {"buildup": [], "festival": [], "teardown": []}
    for shift_type, shift_time_slots in remaining_shifts.items():
        for time_slot, locations in shift_time_slots.items():
            category = category_for_time_slot(time_slot)
            if not category:
                continue
            for location, vacancies in locations.items():
                for _ in range(vacancies):
                    empty_shifts[category].append({
                        "shift_type": shift_type,
                        "location": location,
                        "time_slot": time_slot,
                        "volunteer_id": ""
                    })

    master_schedule = (
        assigned_shifts["buildup"] + assigned_shifts["festival"] + assigned_shifts["teardown"]
        + empty_shifts["buildup"] + empty_shifts["festival"] + empty_shifts["teardown"]
    )
    write_schedule(master_path, master_schedule)
    write_schedule(buildup_path, assigned_shifts["buildup"] + empty_shifts["buildup"])
    write_schedule(festival_path, assigned_shifts["festival"] + empty_shifts["festival"])
    write_schedule(teardown_path, assigned_shifts["teardown"] + empty_shifts["teardown"])

def main() -> int:
    args = parse_args()
    setup_logging(args)

    shifts_path = args.shifts or Path("Input/shifts.json")
    volunteers_path = args.csv or Path("Input/survey.csv")
    output_dir = args.output or Path("Output")

    shifts_available = initialise_shifts(shifts_path)
    #print(shifts_available)
    volunteers = read_volunteers(volunteers_path)
    #print(volunteers)
    shifts = assign_shifts(volunteers, shifts_available)
    #print(shifts)
    write_master_schedule(shifts, volunteers, shifts_available, output_dir)

    return 0

if __name__ == "__main__":
    exit(main())