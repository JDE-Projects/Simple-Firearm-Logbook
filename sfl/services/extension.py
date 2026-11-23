"""Read optional UI extension files supplied by an embedding application."""

from sfl.debug_log import describe_error


def read_extension_files(script_path, stylesheet_path, log):
    """Return the available extension text and report any file-read failures."""
    errors = []

    def read_file(path, kind):
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as file:
                return file.read()
        except FileNotFoundError as error:
            message = f"Couldn't read extension file {path}: file was not found."
            error_name = describe_error(error)
        except UnicodeDecodeError as error:
            message = f"Couldn't read extension file {path}: it is not valid UTF-8."
            error_name = describe_error(error)
        except OSError as error:
            message = f"Couldn't read extension file {path}: {error}."
            error_name = describe_error(error)
        errors.append(message)
        try:
            log(f"Couldn't read extension {kind} file ({error_name})")
        except Exception:
            pass
        return None

    return {
        "ok": True,
        "script": read_file(script_path, "script"),
        "stylesheet": read_file(stylesheet_path, "stylesheet"),
        "errors": errors,
    }
