from dotenv import load_dotenv
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse
from socket import gaierror

import argparse
import logging
import mimetypes
import os
import smtplib
import ssl
import validators


# ===
# Helper Functions
# ===
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-labeling pipeline with Detection, SAM, and CLIP validation.")

    parser.add_argument(
        "-i", "--input",
        type=Path,
        help="Path to email content text file"
    )

    parser.add_argument(
        "-m", "--manifest",
        type=Path,
        help="Path to manifest file"
    )

    parser.add_argument(
        "-r", "--receipients",
        nargs='+',
        type=str,
        help="Email receipients"
    )

    parser.add_argument(
        "-s", "--subject",
        type=str,
        default="",
        help="Email subject line"
    )
    
    parser.add_argument(
        "-a", "--attachments-dir",
        type=Path,
        help="Path to directory containing attachments for all emails"
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

def setup_logging(args: argparse.Namespace) -> int:
    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    elif args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.quiet:
        logging.basicConfig(level=logging.CRITICAL)
    else:
        logging.basicConfig(level=logging.WARNING)

def send_mail(receipients: list[tuple[str | None, str]], subject: str, message: str, config: dict,
              static_attachments: list = None) -> list[str]:
    failed = []
    context = ssl.create_default_context()
    
    server = None
    try:
        if config['port'] == 465:
            server = smtplib.SMTP_SSL(host=config['host'], port=config['port'], context=context)
        else:
            server = smtplib.SMTP(host=config['host'], port=config['port'])
            if config['use_tls']:
                server.starttls(context=context)
        server.login(user=config['user'], password=config['password'])
    except (gaierror, ConnectionRefusedError):  # Unable to connect to the server specified in the configuration file
        logging.error('Failed to connect to the server. Bad connection settings?')
        return [r for _, r in receipients]
    except smtplib.SMTPServerDisconnected:  # Unable to maintain connection; usually authentication error (username/password)
        logging.error('Failed to connect to the server. Wrong user/password?')
        return [r for _, r in receipients]
    except smtplib.SMTPException as e:  # Unable to send email for some other reason
        logging.error('SMTP error occurred during connection: ' + str(e))
        return [r for _, r in receipients]
    except Exception as e:
        logging.error('General error occurred during connection: ' + str(e))
        return [r for _, r in receipients]

    for i, (ics_file, receipient) in enumerate(receipients):
        email = EmailMessage()
        email.set_content(message)
        email['Subject'] = subject
        email['From'] = config['from_address']
        email['To'] = receipient

        if static_attachments:
            for filename, maintype, subtype, data in static_attachments:
                email.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

        if ics_file and os.path.isfile(ics_file):
            try:
                with open(ics_file, 'rb') as f:
                    ics_data = f.read()
                email.add_attachment(ics_data, maintype='text', subtype='calendar', filename=os.path.basename(ics_file))
            except Exception as e:
                logging.error(f"Failed to attach ICS file {ics_file} for {receipient}: {e}")

        try:
            server.send_message(email)
            logging.info(f"Sent ({i+1}/{len(receipients)}) emails.")
        except Exception as e:
            logging.error(f"General error sending to {receipient}: {e}")
            failed.append(receipient)
        
    try:
        server.quit()
    except Exception:
        pass
        
    return failed

def read_email_content(text_file: Path) -> str | None:
    try:
        with open(text_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        logging.error(f"Unable to read text file: {e}")
        return None
    return content

def read_receipients(path: Path) -> list[tuple[str | None, str]] | None:
    receipients: list[tuple[str | None, str]] = []

    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                ics_file, receipient_address = line.strip().split(',')

                if validators.email(receipient_address):
                    receipients.append((ics_file, receipient_address))
                else:
                    logging.warning(f"Invalid email address '{receipient_address}' discarded.")
    except Exception as e:
        logging.error(f"Unable to read text file: {e}")
        return None
    return receipients

def load_static_attachments(attachments_dir: Path) -> list:
    attachments = []
    if not attachments_dir or not os.path.isdir(attachments_dir):
        return attachments
    
    for filename in os.listdir(attachments_dir):
        file_path = os.path.join(attachments_dir, filename)
        if os.path.isfile(file_path):
            try:
                with open(file_path, 'rb') as f:
                    data = f.read()
                
                ctype, encoding = mimetypes.guess_type(file_path)
                if ctype is None or encoding is not None:
                    ctype = 'application/octet-stream'
                maintype, subtype = ctype.split('/', 1)
                
                attachments.append((filename, maintype, subtype, data))
            except Exception as e:
                logging.error(f"Failed to read attachment {file_path}: {e}")
                
    return attachments

def main() -> int:
    args: argparse.Namespace = parse_args()
    setup_logging(args)

    attachments_dir = args.attachments_dir or (Path(__file__).parent / "Input/email/attachments")
    static_attachments = load_static_attachments(attachments_dir)

    manifest_file = args.manifest or (Path(__file__).parent / "Output/ics/manifest.txt")
    receipients = read_receipients(manifest_file)
            
    failed: list[str] = []

    if receipients is None:
        return 1
    elif not receipients:
        logging.warning("No receipients. Exiting...")
        return 0
    
    input_file = args.input or (Path(__file__).parent / "Input/email/text.txt")
    content = read_email_content(input_file)
    if content is None:
        return 1
    
    subject = args.subject or input("Subject line is empty. Enter a new subject line or leave empty: ").strip()

    email_config = {
        'host': None,
        'port': 587,
        'user': None,
        'password': None,
        'use_tls': True,
        'from_address': None
    }

    try:
        load_dotenv(dotenv_path=Path(__file__).parent / ".env")
        email_config['host'] = os.getenv('EMAIL_HOST')
        email_config['port'] = int(os.getenv('EMAIL_PORT'))
        email_config['user'] = os.getenv('EMAIL_USER')
        email_config['password'] = os.getenv('EMAIL_PASSWORD')
        email_config['use_tls'] = os.getenv('EMAIL_USE_TLS', 'True').lower() == 'true'
        email_config['from_address'] = os.getenv('EMAIL_FROM')
    except Exception as e:
        logging.error(f"Variable loading failed. {e}")
        return 1
    
    # Send test email first
    logging.info("Sending test email to self...")
    test_receipients = [(None, email_config['from_address'])]
    if send_mail(receipients=test_receipients, subject=f"Test: {subject}", message=content, config=email_config, static_attachments=static_attachments):
        logging.error("Test email failed. Check connection settings and try again.")
        return 1

    command = input("Test email sent successfully. Enter 'CONTINUE' in all caps to proceed with sending to all receipients: ").strip()
    if command != 'CONTINUE':
        exit(0)

    logging.info(f"Starting to send emails to {len(receipients)} people...")
    
    failed = send_mail(receipients=receipients, subject=subject, message=content, config=email_config, static_attachments=static_attachments)

    if failed:
        logging.warning(f"Sending failed for: {failed}")
    
    logging.info(f"Sending complete. Sent email to {len(receipients) - len(failed)} receipients.")

    return 0

if __name__ == '__main__':
    exit(main())
