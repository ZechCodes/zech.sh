"""Import posts admin controller."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.response import Template as TemplateResponse
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.admin.helpers import get_admin_context
from skrift.admin.navigation import ADMIN_NAV_TAG
from skrift.auth.guards import Permission, auth_guard
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.db.services import page_service
from skrift.lib.flash import (
    flash_error,
    flash_success,
    flash_warning,
    get_flash_messages,
)


class ImportPostsAdminController(Controller):
    """Controller for bulk importing posts from XML."""

    path = "/admin"
    guards = [auth_guard]

    @get(
        "/import-posts",
        tags=[ADMIN_NAV_TAG],
        guards=[auth_guard, Permission("manage-posts")],
        opt={"label": "Import Posts", "icon": "upload", "order": 95},
    )
    async def import_posts_form(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        """Show the import posts upload form."""
        ctx = await get_admin_context(request, db_session)
        flash_messages = get_flash_messages(request)
        return TemplateResponse(
            "admin/import_posts.html",
            context={
                "flash_messages": flash_messages,
                "results": None,
                **ctx,
            },
        )

    @post(
        "/import-posts",
        guards=[auth_guard, Permission("manage-posts")],
    )
    async def import_posts(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        """Parse uploaded XML and import posts."""
        ctx = await get_admin_context(request, db_session)
        results: list[dict] = []
        user_id = request.session.get(SESSION_USER_ID)

        form = await request.form()
        upload = form.get("xml_file")

        if not upload or not hasattr(upload, "read"):
            flash_error(request, "No file uploaded")
            flash_messages = get_flash_messages(request)
            return TemplateResponse(
                "admin/import_posts.html",
                context={"flash_messages": flash_messages, "results": None, **ctx},
            )

        file_bytes = await upload.read()

        try:
            root = ET.fromstring(file_bytes)
        except ET.ParseError as exc:
            flash_error(request, f"Invalid XML: {exc}")
            flash_messages = get_flash_messages(request)
            return TemplateResponse(
                "admin/import_posts.html",
                context={"flash_messages": flash_messages, "results": None, **ctx},
            )

        posts = root.findall("post")
        if not posts:
            flash_warning(request, "No <post> elements found in the XML file")
            flash_messages = get_flash_messages(request)
            return TemplateResponse(
                "admin/import_posts.html",
                context={"flash_messages": flash_messages, "results": None, **ctx},
            )

        created_count = 0
        skipped_count = 0
        error_count = 0

        for post_el in posts:
            title = (post_el.findtext("title") or "").strip()
            slug = (post_el.findtext("slug") or "").strip()

            if not title or not slug:
                results.append(
                    {
                        "status": "error",
                        "title": title or "(missing)",
                        "slug": slug or "(missing)",
                        "detail": "Missing required <title> or <slug>",
                    }
                )
                error_count += 1
                continue

            # Check for duplicate slug
            existing = await page_service.get_page_by_slug(
                db_session, slug, page_type="post"
            )
            if existing:
                results.append(
                    {
                        "status": "skipped",
                        "title": title,
                        "slug": slug,
                        "detail": "Post with this slug already exists",
                    }
                )
                skipped_count += 1
                continue

            content = (post_el.findtext("content") or "").strip()
            meta_description = (post_el.findtext("meta_description") or "").strip() or None
            order_text = (post_el.findtext("order") or "0").strip()
            is_published_text = (post_el.findtext("is_published") or "true").strip().lower()
            published_at_text = (post_el.findtext("published_at") or "").strip()

            is_published = is_published_text in ("true", "1", "yes")

            published_at = None
            if published_at_text:
                try:
                    published_at = datetime.fromisoformat(published_at_text)
                except ValueError:
                    results.append(
                        {
                            "status": "error",
                            "title": title,
                            "slug": slug,
                            "detail": f"Invalid published_at date: {published_at_text}",
                        }
                    )
                    error_count += 1
                    continue

            try:
                order = int(order_text)
            except ValueError:
                order = 0

            try:
                await page_service.create_page(
                    db_session,
                    slug=slug,
                    title=title,
                    content=content,
                    is_published=is_published,
                    published_at=published_at,
                    user_id=UUID(user_id) if user_id else None,
                    order=order,
                    meta_description=meta_description,
                    page_type="post",
                )
                results.append(
                    {
                        "status": "created",
                        "title": title,
                        "slug": slug,
                        "detail": "Imported successfully",
                    }
                )
                created_count += 1
            except Exception as exc:
                results.append(
                    {
                        "status": "error",
                        "title": title,
                        "slug": slug,
                        "detail": str(exc),
                    }
                )
                error_count += 1

        # Summary flash message
        if error_count > 0:
            flash_error(
                request,
                f"Import finished with errors: {created_count} created, "
                f"{skipped_count} skipped, {error_count} failed",
            )
        elif skipped_count > 0:
            flash_warning(
                request,
                f"Import complete: {created_count} created, {skipped_count} skipped (duplicate slugs)",
            )
        else:
            flash_success(request, f"Import complete: {created_count} posts created")

        flash_messages = get_flash_messages(request)
        return TemplateResponse(
            "admin/import_posts.html",
            context={
                "flash_messages": flash_messages,
                "results": results,
                **ctx,
            },
        )
