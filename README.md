# Campusfestival Shift Management

A suite of Python scripts for managing volunteer shift schedules for the Campusfestival. It automates assigning volunteers to shifts based on their preferences and availability, generating calendar invites, and sending out schedule emails. 

## Shift Generator (`shift-generator.py`)

Generates the shift schedules by assigning volunteers to shifts based on availability and preferences.

### Constraints
- Initially, each volunteer is assigned up to `MAX_SHIFTS_BUILDUP` build-up shift if available.
- Initially, each volunteer is assigned up to `MAX_SHIFTS_FESTIVAL` festival shifts if available.
- Initially, each volunteer is assigned up to `MAX_SHIFTS_TEARDOWN` tear-down shift if available.
- **Festival shifts**:
  - During the first pass, volunteers are assigned to festival shifts based on their preferences, if possible.
  - Remaining festival shifts are filled in a second pass without considering preferences.

### Input Data
- **Shifts Data (`-s`, `--shifts`)**: A JSON file (default `Input/shifts.json`) containing the available shifts format:
  ```json
  [
      {
          "shift_type": "Schankwagen",
          "first_shift_start": "2026-06-05T13:00:00+02:00",
          "shift_duration": 2,
          "locations": ["Wagen 1", "Wagen 2"],
          "number_of_shifts": [5, 5],
          "people_per_shift": [6, 4]
      }
  ]
  ```
  Lists or nested lists may be used for `first_shift_start`, `shift_duration` and `people_per_shift` to specify different values per location/shift.
- **Volunteers Data (`-c`, `--csv`)**: A CSV file (default `Input/survey.csv`) with volunteer details. Multi-select options in availability and task preferences should be separated by a semicolon and space (`"; "`).

### Output
- Generates master and individual schedules in the output directory (`-o`, `--output`, default: `Output/`):
  - `master_schedule.csv`
  - `buildup_schedule.csv`
  - `festival_schedule.csv`
  - `teardown_schedule.csv`

## Calendar Generator (`generate-ics.py`)

Generates calendar files and a recipient manifest out of the master schedule.

### Input Data
- **Schedule File (`-f`, `--file`)**: The path to the generated `master_schedule.csv` file (required).
- **Output Directory (`-o`, `--output`)**: Directory where the files will be saved (default: `Output/`).

### Output
- Creates an `.ics` calendar file for each volunteer.
- Creates a manifest file inside the output directory mapping the generated `.ics` files to the volunteers' email addresses.

## PDF Generator (`generate-pdf.py`)

Generates a single PDF overview of the master schedule, with one page per shift type and location.

### Input Data
- **Schedule File (`-f`, `--file`)**: The path to the generated `master_schedule.csv` file (default: `Output/master_schedule.csv`).
- **Output Directory (`-o`, `--output`)**: Directory where the PDF will be saved (default: `Output/`).

### Output
- Creates a single PDF file named `Schedules.pdf` in the output directory.

## Email Sender (`send-mail.py`)

Sends calendar invites and custom text emails to the volunteers via SMTP. SMTP details are loaded via configuration dict / environment variables.

### Input Data
- **Email Content (`-i`, `--input`)**: Path to a plain text file containing the email body (required).
- **Manifest (`-m`, `--manifest`)**: Path to the generated manifest file from `generate-ics.py`. The file expects CSV lines mapping ICS files to emails (`some_file.ics,email@domain.com`).
- **Subject (`-s`, `--subject`)**: Subject line for the email.
- **Recipients (`-r`, `--receipients`)**: Optional list of explicit email recipients.
- **Static Attachments (`-a`, `--attachments-dir`)**: Directory containing additional files to attach statically to every email.

### Output
- Initially sends a test email and awaits user confirmation.
- After user confirmation, sends the emails via configured SMTP server to the volunteers alongside their custom `.ics` calendar files and all static attachments.
