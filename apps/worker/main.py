from packages.data.queue.rq_worker import WorkerSettings, run_worker


def main() -> None:
    settings = WorkerSettings.from_environment()
    run_worker(settings)


if __name__ == "__main__":
    main()
