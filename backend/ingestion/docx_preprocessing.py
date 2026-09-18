"""
DOCX preprocessing pipeline.

Enterprise Compliance Intelligence Platform

Purpose
-------
Process Microsoft Word documents into the same format produced by
PdfChunking.

Output
------
texts  : List[str]
images : List[dict]   — each dict has image_path, description, chunk_order_index

The interface intentionally matches PdfChunking so MMKGBuilder can
switch processors based only on file extension.

Image handling
--------------
Embedded images are extracted from the DOCX zip archive and described
via the same multimodal LLM pipeline used by PdfChunking.  Previously
the description field was always an empty string, which produced useless
nodes in the knowledge graph.  Images that fail description get a clear
fallback message rather than silently empty text.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import zipfile

from docx import Document

from ..config import settings as parameter
from ..ingestion.image_utils import compress_image_to_size, get_image_description
from ..storage.kv_storage import JsonKVStorage
from ..utils.base import load_json, logger


class DocxChunking:

    def __init__(
        self,
        docx_path: str,
        working_dir: str,
    ):
        self.docx_path   = docx_path
        self.working_dir = working_dir
        self.images_dir  = os.path.join(working_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)

    # ---------------------------------------------------------
    # Public Entry
    # ---------------------------------------------------------

    async def process(self) -> tuple[list[str], list[dict]]:

        logger.info("📄 Processing DOCX document...")

        texts  = self._extract_text()
        images = await self._extract_images_async()

        logger.info(
            "✅ DOCX Parsed (%d text blocks, %d images)",
            len(texts), len(images),
        )

        return texts, images

    # ---------------------------------------------------------
    # Text Extraction
    # ---------------------------------------------------------

    def _extract_text(self) -> list[str]:

        document   = Document(self.docx_path)
        paragraphs = []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if text:
                paragraphs.append(text)

        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    paragraphs.append(" | ".join(cells))

        return paragraphs

    # ---------------------------------------------------------
    # Embedded Image Extraction + LLM Description
    # ---------------------------------------------------------

    async def _extract_images_async(self) -> list[dict]:
        """Extract embedded images from the DOCX zip archive and describe
        each one via the multimodal LLM (same pipeline as PdfChunking).

        A semaphore of 16 matches the limit used elsewhere in the pipeline
        so we never open more simultaneous vision-LLM requests than the
        rate-limit budget allows.
        """
        raw_images = self._unpack_images()

        if not raw_images:
            return []

        # Load existing text chunks so we can assign chunk_order_index
        text_chunks_all = load_json(
            os.path.join(self.working_dir, "kv_store_text_chunks.json")
        ) or {}

        cache_kv = JsonKVStorage(
            namespace="multimodel_llm_response_cache",
            storage_dir=parameter.CACHE_PATH,
        )

        semaphore = asyncio.Semaphore(16)

        async def _describe(img_path: str) -> dict:
            async with semaphore:
                try:
                    desc, _seg = await get_image_description(
                        img_path,
                        caption=[],
                        footnote=[],
                        context="",          # DOCX images have no inline context
                        hashing_kv=cache_kv,
                    )
                except Exception as exc:
                    logger.warning(
                        "⚠️ [DocxChunking] Description failed for %s: %s — "
                        "using fallback text.",
                        os.path.basename(img_path), exc,
                    )
                    desc = "Image description generation failed."

            if not desc or not desc.strip():
                logger.warning(
                    "⚠️ [DocxChunking] Empty description returned for %s — "
                    "using fallback text.  This image will produce a low-quality "
                    "node in the knowledge graph.",
                    os.path.basename(img_path),
                )
                desc = "No description available."

            return {
                "image_path":        img_path,
                "description":       desc,
                "chunk_order_index": 0,   # DOCX images are not tied to specific chunks
            }

        logger.info(
            "🖼️ Describing %d DOCX image(s) concurrently (semaphore=16) …",
            len(raw_images),
        )
        results = await asyncio.gather(*[_describe(p) for p in raw_images])
        await cache_kv.index_done_callback()
        return list(results)

    def _unpack_images(self) -> list[str]:
        """Unzip the DOCX and copy every media file into self.images_dir.
        Returns a list of destination file paths for image files only.
        """
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp"}

        tmp_dir = os.path.join(self.working_dir, "_docx_tmp")
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir, exist_ok=True)

        extracted_paths: list[str] = []

        try:
            with zipfile.ZipFile(self.docx_path, "r") as archive:
                archive.extractall(tmp_dir)

            media_dir = os.path.join(tmp_dir, "word", "media")
            if not os.path.exists(media_dir):
                logger.info(
                    "ℹ️ [DocxChunking] No embedded media found in '%s'.",
                    os.path.basename(self.docx_path),
                )
                return []

            for filename in os.listdir(media_dir):
                ext = os.path.splitext(filename)[1].lower()
                if ext not in IMAGE_EXTS:
                    # Skip non-image media (emf, wmf, etc.)
                    logger.debug(
                        "[DocxChunking] Skipping non-image media file: %s", filename
                    )
                    continue

                source      = os.path.join(media_dir, filename)
                destination = os.path.join(self.images_dir, filename)
                shutil.copy2(source, destination)
                extracted_paths.append(destination)

        except zipfile.BadZipFile as exc:
            logger.warning(
                "⚠️ [DocxChunking] Could not unzip '%s': %s — no images extracted.",
                self.docx_path, exc,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return extracted_paths
