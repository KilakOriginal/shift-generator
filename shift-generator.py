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

from datetime import timedelta
from icalendar import Calendar, Event, vText
from pathlib import Path
from random import shuffle

import argparse
import dateutil.parser
import json
import logging


COL_ID = "Benutzer-ID"
COL_FIRST_NAME = "Vorname"
COL_LAST_NAME = "Nachname"
COL_EMAIL = "E-Mail-Adresse unter der du zuverlässig erreichbar bist."
COL_PHONE = "Telefonnummer - Wir planen eine WhatsApp/Signal-Gruppe für das Team zu erstellen."
COL_AVAILABILITY = "Aufbau / Abbau"
COL_TASK_PREFERENCE = "Gibt es einen Bereich in dem du besonders gerne eingesetzt werden möchtest? Wähle gerne mehrere aus, wir können leider noch keine Zuteilung garantieren."

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
    "Cocktailbar": 0b100,
    "Veranstaltungstechnik - Event Technology": 0b100,
    "Merchverkauf - Merch sales": 0b100,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign shifts automatically")

    parser.add_argument(
        "-s", "--shifts",
        type=Path,
        help="Path to shifts json file")

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

def read_volunteers(input_csv: Path) -> dict:
    """
    Generate a dict that contains volunteer IDs as keys and their data (name, task bitmask, email, phone) as values.
    """
    volunteers = {}
    with input_csv.open() as f:
        # Skip header
        next(f)

        for line in f:
            fields = line.strip().split(",")
            volunteer_id = fields[0].strip('"')
            first_name = fields[3].strip('"')
            last_name = fields[4].strip('"')
            email = fields[5].strip('"')
            phone = fields[6].strip('"')
            availability = fields[7].strip('"').split("; ")
            availability = split_availability(availability)
            task_preference = fields[9].strip('"').split("; ")

            task_bitmask = 0
            for task in task_preference:
                if task in TASK_MASK:
                    task_bitmask |= TASK_MASK[task]

            volunteers[volunteer_id] = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "availability_buildup": availability[0],
                "availability_festival": availability[1],
                "availability_teardown": availability[2],
                "task_bitmask": task_bitmask
            }

    return volunteers

def assign_shifts(volunteers: dict, shifts: dict) -> dict:
    pass

def main() -> int:
    args = parse_args()
    setup_logging(args)

    shifts_path = args.shifts or Path("Input/shifts.json")

    shifts_available = initialise_shifts(shifts_path)

    return 0

if __name__ == "__main__":
    exit(main())