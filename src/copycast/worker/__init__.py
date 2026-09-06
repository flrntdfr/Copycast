"""The worker process: claims jobs from Postgres and runs them through the Engine.

``copycast worker`` calls :func:`copycast.worker.main.run`. The package is an
adapter-level composition: it may import ``copycast.adapters`` and the
application layer, and it talks to yt-dlp only through the Engine port.
"""
