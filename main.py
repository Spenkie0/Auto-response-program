from app.cli import build_parser
from app.config import default_base_dir
from app.errors.handler import error_payload, format_error, save_error_report
from app.workflow import run


if __name__ == "__main__":
    args = build_parser().parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Stopped by user.")
    except Exception as exc:
        payload = error_payload(
            "UNEXPECTED",
            "main",
            str(exc),
            details={"exception": exc.__class__.__name__},
        )
        print(format_error(payload))
        try:
            path = save_error_report(default_base_dir(), "main", payload, overwrite=args.overwrite)
            print(f"[ERROR REPORT] {path}")
        except OSError as save_exc:
            print(f"[ERROR REPORT FAILED] {save_exc}")
