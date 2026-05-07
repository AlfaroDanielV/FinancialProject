"""Standalone background workers.

Each module in this package is meant to be the entrypoint of an
independent process (e.g. an Azure Container Apps Job invocation), NOT
part of the FastAPI app's lifespan. They share the api/ services and
DB models but never start the bot or HTTP server.
"""
