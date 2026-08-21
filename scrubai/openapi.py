"""Dynamically generated OpenAPI 3.0.3 spec for the service."""

from __future__ import annotations

from typing import Any

from . import __version__
from .metadata import SUPPORTED_FORMATS
from .rewrite import BACKENDS, STRENGTHS


def spec(base_url: str = "http://127.0.0.1:8765") -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "AI Watermark Removal Toolkit",
            "version": __version__,
            "description": (
                "Local service that detects and removes invisible-character "
                "watermarks (Layer A), rewrites text through a second model to "
                "displace statistical watermarks (Layer B), and strips C2PA, "
                "EXIF, XMP, and document metadata from files."
            ),
        },
        "servers": [{"url": base_url}],
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "Health": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "version": {"type": "string"},
                        "uptime_seconds": {"type": "number"},
                    },
                },
                "InspectRequest": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "byte",
                            "description": "base64-encoded file bytes",
                        },
                        "text": {
                            "type": "string",
                            "description": "alternative to file, for plain text",
                        },
                        "name": {"type": "string", "example": "document.docx"},
                        "options": {"$ref": "#/components/schemas/Options"},
                    },
                },
                "Options": {
                    "type": "object",
                    "properties": {
                        "aggressive": {"type": "boolean", "default": False},
                        "strip_emoji_glue": {"type": "boolean", "default": False},
                        "normalize": {"type": "boolean", "default": False},
                        "layer_a": {"type": "boolean", "default": True},
                        "metadata": {"type": "boolean", "default": True},
                        "use_tools": {"type": "boolean", "default": True},
                        "diff": {"type": "boolean", "default": False},
                    },
                },
                "RewriteRequest": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {
                        "text": {"type": "string"},
                        "backend": {"type": "string", "enum": list(BACKENDS)},
                        "strength": {"type": "string", "enum": list(STRENGTHS)},
                        "model": {"type": "string"},
                        "base_url": {"type": "string"},
                        "temperature": {"type": "number", "default": 0.9},
                        "candidates": {"type": "integer", "minimum": 1, "maximum": 5},
                        "allow_remote": {"type": "boolean", "default": False},
                        "layer_a_after": {"type": "boolean", "default": True},
                    },
                },
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                },
            },
        },
        "paths": {
            "/health": {
                "get": {
                    "summary": "Liveness probe",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "service is up",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Health"}
                                }
                            },
                        }
                    },
                }
            },
            "/capabilities": {
                "get": {
                    "summary": "Which optional tools and backends are available",
                    "responses": {"200": {"description": "capability report"}},
                }
            },
            "/openapi.json": {
                "get": {
                    "summary": "This document",
                    "security": [],
                    "responses": {"200": {"description": "OpenAPI spec"}},
                }
            },
            "/inspect": {
                "post": {
                    "summary": "Scan a file or text without modifying it",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/InspectRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "findings"},
                        "400": {
                            "description": "bad request",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Error"}
                                }
                            },
                        },
                        "413": {"description": "payload too large"},
                    },
                }
            },
            "/clean": {
                "post": {
                    "summary": "Strip watermarks and metadata, return the cleaned bytes",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/InspectRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "cleaned file plus a report"},
                        "400": {"description": "bad request"},
                        "413": {"description": "payload too large"},
                    },
                }
            },
            "/rewrite": {
                "post": {
                    "summary": "Layer B: paraphrase through a second model",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RewriteRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "rewritten text with divergence score"},
                        "400": {"description": "bad request"},
                        "502": {"description": "rewrite backend unreachable"},
                    },
                }
            },
        },
        "x-supported-formats": list(SUPPORTED_FORMATS),
        "security": [{"bearerAuth": []}],
    }
