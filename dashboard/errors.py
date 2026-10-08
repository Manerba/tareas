"""Fachfehler mit stabilen Metadaten und unveraenderter REST-detail-Ausgabe."""

from fastapi import HTTPException


class ApplicationError(HTTPException):
    """REST behaelt den detail-Text; andere Transporte nutzen code und Felder."""

    def __init__(
        self, status_code: int, detail: str, *, code: str,
        field: str | None = None, fields: list[str] | None = None,
    ):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.field = field
        self.fields = fields
