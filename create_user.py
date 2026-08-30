# create_user.py — create admin users.
#
# Usage:
#   python3 create_user.py operator you@example.com
#   python3 create_user.py owner bob@bobsplumbing.com 1
#
# Prompts for the password rather than taking it as an argument, so it
# doesn't end up in shell history.

import sys
from getpass import getpass

from dotenv import load_dotenv
load_dotenv()

from db import init_db, create_user, get_all_businesses


def main():
    init_db()

    if len(sys.argv) < 3:
        print(__doc__)
        print("\nAvailable businesses:")
        for b in get_all_businesses():
            print(f"  {b['id']}: {b['name']}")
        return

    role  = sys.argv[1]
    email = sys.argv[2]

    if role == "operator":
        business_id, is_operator = None, True
    elif role == "owner":
        if len(sys.argv) < 4:
            print("Owner requires a business id.")
            return
        business_id, is_operator = int(sys.argv[3]), False
    else:
        print("Role must be 'operator' or 'owner'.")
        return

    password = getpass("Password: ")
    if password != getpass("Confirm: "):
        print("Passwords don't match.")
        return
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return

    try:
        create_user(email, password, business_id, is_operator)
        print("Done.")
    except Exception as e:
        print(f"Failed: {e}")


if __name__ == "__main__":
    main()