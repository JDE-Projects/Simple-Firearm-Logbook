"""Read optional UI extension files supplied by an embedding application."""


def read_extension_files(script_path, stylesheet_path, log):
    """Return the available extension text and report any file-read failures."""
    errors = []

    def read_file(path):
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as file:
                return file.read()
        except FileNotFoundError:
            message = f"Couldn't read extension file {path}: file was not found."
        except UnicodeDecodeError:
            message = f"Couldn't read extension file {path}: it is not valid UTF-8."
        except OSError as error:
            message = f"Couldn't read extension file {path}: {error}."
        errors.append(message)
        try:
            log(message)
        except Exception:
            pass
        return None

    return {
        "ok": True,
        "script": read_file(script_path),
        "stylesheet": read_file(stylesheet_path),
        "errors": errors,
    }
