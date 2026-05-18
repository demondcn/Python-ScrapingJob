from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from sqlalchemy.orm import Session

from .ats_resume_builder import build_ats_filename, build_ats_resume, get_available_targets
from .cv_generator import generate_cv, register_generated_cv
from .database import create_session_factory, create_sqlite_engine, init_db
from .discarded_job_service import (
    count_discarded_jobs,
    clear_discarded_jobs,
    export_discarded_jobs,
    get_discarded_job_by_id,
    list_discarded_jobs,
    parse_text_list,
    reprocess_discarded_jobs,
    short_discard_reason,
)
from .freshness_monitor import retry_pending_alerts, run_fresh_monitor
from .job_service import (
    clear_offers,
    create_offer,
    get_offer_by_id,
    list_fresh_offers,
    list_offers,
    list_pending_alert_offers,
    refresh_offer_match,
    update_offer_notes,
    update_offer_status,
)
from .message_generator import generate_application_message
from .models import JobSearchSource
from .profile_service import get_profile, upsert_profile
from .resume_profile_service import DEFAULT_RESUME_PROFILE_PATH, load_resume_profile, save_resume_profile
from .resume_reader import read_resume_file
from .scrapers.linkedin_selenium_scraper import build_linkedin_jobs_url
from .scrapers.registry import list_supported_portals
from .scrapers.selenium_base import SeleniumJobScraper
from .search_sources import (
    add_source,
    disable_blocked_sources,
    get_source_by_id,
    list_sources,
    set_source_enabled,
    test_source,
    unpause_source_by_id,
    unpause_sources_by_portal,
    update_portal_source_intervals,
    update_source_interval,
)
from .settings import load_settings
from .telegram_notifier import format_job_alert, send_job_alert
from .workflows import run_daily_scan

DEFAULT_DISCARDED_LIST_LIMIT = 20
LINKEDIN_HOME_URL = "https://www.linkedin.com/"
LINKEDIN_LOGIN_PROFILE_MESSAGE = "Inicia sesión manualmente en LinkedIn y luego cierra Chrome."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JobOps Personal Assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Crea o migra las tablas de SQLite")

    profile_parser = subparsers.add_parser("profile", help="Gestiona el perfil")
    profile_sub = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_sub.add_parser("show", help="Muestra el perfil")
    profile_show.set_defaults(handler=_handle_profile_show)

    profile_set = profile_sub.add_parser("set", help="Crea o actualiza el perfil")
    for arg in (
        "full-name",
        "email",
        "phone",
        "city",
        "summary",
        "skills",
        "projects",
        "education",
        "target-roles",
    ):
        profile_set.add_argument(f"--{arg}", required=True)
    profile_set.set_defaults(handler=_handle_profile_set)

    offer_parser = subparsers.add_parser("offer", help="Gestiona ofertas")
    offer_sub = offer_parser.add_subparsers(dest="offer_command", required=True)

    offer_add = offer_sub.add_parser("add", help="Agrega una oferta")
    for arg in ("title", "url"):
        offer_add.add_argument(f"--{arg}", required=True)
    for arg in ("company", "portal", "location", "modality", "salary", "description", "requirements", "notes"):
        offer_add.add_argument(f"--{arg}", default="")
    offer_add.set_defaults(handler=_handle_offer_add)

    offer_list = offer_sub.add_parser("list", help="Lista ofertas")
    offer_list.add_argument("--portal")
    offer_list.set_defaults(handler=_handle_offer_list)

    offer_fresh = offer_sub.add_parser("fresh", help="Muestra ofertas frescas")
    offer_fresh.add_argument("--portal")
    offer_fresh.add_argument("--hours", type=int, default=24)
    offer_fresh.set_defaults(handler=_handle_offer_fresh)

    offer_show = offer_sub.add_parser("show", help="Ver detalle")
    offer_show.add_argument("--id", required=True, type=int)
    offer_show.set_defaults(handler=_handle_offer_show)

    offer_status = offer_sub.add_parser("update-status", help="Actualiza estado")
    offer_status.add_argument("--id", required=True, type=int)
    offer_status.add_argument("--status", required=True)
    offer_status.set_defaults(handler=_handle_offer_status)

    offer_notes = offer_sub.add_parser("update-notes", help="Actualiza notas")
    offer_notes.add_argument("--id", required=True, type=int)
    offer_notes.add_argument("--notes", required=True)
    offer_notes.set_defaults(handler=_handle_offer_notes)

    offer_message = offer_sub.add_parser("generate-message", help="Genera mensaje")
    offer_message.add_argument("--id", required=True, type=int)
    offer_message.set_defaults(handler=_handle_offer_message)

    offer_cv = offer_sub.add_parser("generate-cv", help="Genera CV DOCX")
    offer_cv.add_argument("--id", required=True, type=int)
    offer_cv.set_defaults(handler=_handle_offer_cv)

    offer_clear = offer_sub.add_parser("clear", help="Borra ofertas guardadas y hashes vistos")
    offer_clear.add_argument("--portal")
    offer_clear.add_argument("--yes", action="store_true")
    offer_clear.set_defaults(handler=_handle_offer_clear)

    offer_pending = offer_sub.add_parser("pending-alerts", help="Lista ofertas pendientes de alerta por Telegram")
    offer_pending.add_argument("--portal")
    offer_pending.set_defaults(handler=_handle_offer_pending_alerts)

    discarded_parser = subparsers.add_parser("discarded", help="Audita ofertas descartadas")
    discarded_sub = discarded_parser.add_subparsers(dest="discarded_command", required=True)

    discarded_list = discarded_sub.add_parser("list", help="Lista ofertas descartadas")
    discarded_list.add_argument("--portal")
    discarded_list.add_argument("--target-role")
    discarded_list.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_DISCARDED_LIST_LIMIT,
        help=f"Maximo de resultados a mostrar (default: {DEFAULT_DISCARDED_LIST_LIMIT})",
    )
    discarded_list.set_defaults(handler=_handle_discarded_list)

    discarded_show = discarded_sub.add_parser("show", help="Muestra el detalle de una descartada")
    discarded_show.add_argument("--id", required=True, type=int)
    discarded_show.set_defaults(handler=_handle_discarded_show)

    discarded_clear = discarded_sub.add_parser("clear", help="Borra ofertas descartadas")
    discarded_clear.add_argument("--portal")
    discarded_clear.add_argument("--target-role")
    discarded_clear.add_argument("--yes", action="store_true")
    discarded_clear.set_defaults(handler=_handle_discarded_clear)

    discarded_reprocess = discarded_sub.add_parser("reprocess", help="Reprocesa ofertas descartadas con el matcher actual")
    discarded_reprocess.add_argument("--id", type=int)
    discarded_reprocess.add_argument("--portal")
    discarded_reprocess.add_argument("--target-role")
    discarded_reprocess.set_defaults(handler=_handle_discarded_reprocess)

    discarded_export = discarded_sub.add_parser("export", help="Exporta ofertas descartadas a CSV o JSON")
    discarded_export.add_argument("--file", required=True)
    discarded_export.add_argument("--portal")
    discarded_export.add_argument("--target-role")
    discarded_export.set_defaults(handler=_handle_discarded_export)

    sources_parser = subparsers.add_parser("sources", help="Gestiona fuentes de scraping responsable")
    sources_sub = sources_parser.add_subparsers(dest="sources_command", required=True)

    sources_add = sources_sub.add_parser("add", help="Agrega una fuente publica")
    sources_add.add_argument("--portal", required=True)
    sources_add.add_argument("--target-role", required=True)
    sources_add.add_argument("--url", required=True)
    sources_add.add_argument("--interval", type=int, default=15)
    sources_add.add_argument("--keywords", default="")
    sources_add.add_argument("--location", default="")
    sources_add.set_defaults(handler=_handle_sources_add)

    sources_list = sources_sub.add_parser("list", help="Lista fuentes configuradas")
    sources_list.set_defaults(handler=_handle_sources_list)

    sources_enable = sources_sub.add_parser("enable", help="Activa una fuente")
    sources_enable.add_argument("--id", required=True, type=int)
    sources_enable.set_defaults(handler=_handle_sources_enable)

    sources_disable = sources_sub.add_parser("disable", help="Desactiva una fuente")
    sources_disable.add_argument("--id", required=True, type=int)
    sources_disable.set_defaults(handler=_handle_sources_disable)

    sources_test = sources_sub.add_parser("test", help="Prueba una fuente sin guardar resultados")
    sources_test.add_argument("--id", required=True, type=int)
    sources_test.add_argument("--debug-html", action="store_true")
    sources_test.add_argument("--show-discarded", action="store_true")
    sources_test.set_defaults(handler=_handle_sources_test)

    sources_update_interval = sources_sub.add_parser("update-interval", help="Actualiza el intervalo de una fuente o portal")
    sources_update_interval.add_argument("--id", type=int)
    sources_update_interval.add_argument("--portal")
    sources_update_interval.add_argument("--interval", required=True, type=int)
    sources_update_interval.set_defaults(handler=_handle_sources_update_interval)

    sources_unpause = sources_sub.add_parser("unpause", help="Reanuda una fuente pausada o un portal completo")
    sources_unpause.add_argument("--id", type=int)
    sources_unpause.add_argument("--portal")
    sources_unpause.set_defaults(handler=_handle_sources_unpause)

    sources_disable_blocked = sources_sub.add_parser("disable-blocked", help="Desactiva fuentes con bloqueos repetidos")
    sources_disable_blocked.set_defaults(handler=_handle_sources_disable_blocked)

    selenium_parser = subparsers.add_parser("selenium", help="Prueba scrapers opcionales con Selenium")
    selenium_sub = selenium_parser.add_subparsers(dest="selenium_command", required=True)
    selenium_test = selenium_sub.add_parser("test", help="Prueba una URL publica con Selenium sin guardar resultados")
    selenium_test.add_argument("--portal", required=True, choices=("indeed", "linkedin", "indeed_selenium", "linkedin_selenium"))
    selenium_test.add_argument("--url")
    selenium_test.add_argument("--keyword")
    selenium_test.add_argument("--location")
    selenium_test.add_argument("--date-posted", default="24h", choices=("any", "24h", "week", "month"))
    selenium_test.add_argument(
        "--experience-level",
        dest="experience_levels",
        action="append",
        choices=("internship", "entry_level", "associate"),
    )
    selenium_test.add_argument(
        "--workplace",
        dest="workplace_types",
        action="append",
        choices=("onsite", "remote", "hybrid"),
    )
    selenium_test.add_argument("--target-role", required=True)
    selenium_test.set_defaults(handler=_handle_selenium_test)

    linkedin_parser = subparsers.add_parser("linkedin", help="Utilidades de LinkedIn con perfil local de Chrome")
    linkedin_sub = linkedin_parser.add_subparsers(dest="linkedin_command", required=True)
    linkedin_login_profile = linkedin_sub.add_parser(
        "login-profile",
        help="Abre LinkedIn con el perfil local de Chrome para iniciar sesion manualmente",
    )
    linkedin_login_profile.set_defaults(handler=_handle_linkedin_login_profile)
    linkedin_profile_info = linkedin_sub.add_parser(
        "profile-info",
        help="Muestra la configuracion del perfil local de Chrome para Selenium",
    )
    linkedin_profile_info.set_defaults(handler=_handle_linkedin_profile_info)

    monitor_parser = subparsers.add_parser("monitor", help="Monitorea ofertas frescas")
    monitor_sub = monitor_parser.add_subparsers(dest="monitor_command", required=True)

    monitor_fresh = monitor_sub.add_parser("fresh", help="Ejecuta el monitor una vez")
    monitor_fresh.add_argument("--notify-pending", action="store_true")
    monitor_fresh.set_defaults(handler=_handle_monitor_fresh)

    monitor_watch = monitor_sub.add_parser("watch", help="Ejecuta el monitor en bucle")
    monitor_watch.add_argument("--interval", type=int, default=15)
    monitor_watch.set_defaults(handler=_handle_monitor_watch)

    scan_parser = subparsers.add_parser("scan-daily", help="Ejecuta flujo diario basado en Gmail")
    scan_parser.set_defaults(handler=_handle_scan_daily)

    summary_parser = subparsers.add_parser("send-summary", help="Envia resumen de la oferta mas relevante")
    summary_parser.set_defaults(handler=_handle_send_summary)

    notifications_parser = subparsers.add_parser("notifications", help="Gestiona reintentos de notificaciones")
    notifications_sub = notifications_parser.add_subparsers(dest="notifications_command", required=True)
    notifications_retry = notifications_sub.add_parser("retry-pending", help="Reintenta ofertas pendientes de alerta")
    notifications_retry.add_argument("--portal")
    notifications_retry.set_defaults(handler=_handle_notifications_retry_pending)

    resume_parser = subparsers.add_parser("resume", help="Gestiona la hoja de vida base y CV ATS")
    resume_sub = resume_parser.add_subparsers(dest="resume_command", required=True)

    resume_import = resume_sub.add_parser("import", help="Importa una hoja de vida base")
    resume_import.add_argument("--file", required=True)
    resume_import.set_defaults(handler=_handle_resume_import)

    resume_show = resume_sub.add_parser("show", help="Muestra el perfil detectado de la hoja de vida")
    resume_show.set_defaults(handler=_handle_resume_show)

    resume_targets = resume_sub.add_parser("targets", help="Lista perfiles objetivo disponibles")
    resume_targets.set_defaults(handler=_handle_resume_targets)

    resume_generate = resume_sub.add_parser("generate-ats", help="Genera un CV ATS adaptado")
    resume_generate.add_argument("--target", required=True)
    resume_generate.add_argument("--job-id", type=int)
    resume_generate.set_defaults(handler=_handle_resume_generate_ats)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()
    engine = create_sqlite_engine(settings.db_path)
    session_factory = create_session_factory(engine)

    if args.command == "init-db":
        init_db(engine)
        print(f"Base de datos inicializada en: {settings.db_path}")
        return 0

    init_db(engine)
    with session_factory() as session:
        return args.handler(args, session, settings, session_factory)


def _handle_profile_show(args, session: Session, settings, session_factory) -> int:
    profile = get_profile(session)
    if profile is None:
        print("No hay perfil registrado.")
        return 1
    print(f"Nombre: {profile.full_name}")
    print(f"Email: {profile.email}")
    print(f"Telefono: {profile.phone}")
    print(f"Ciudad: {profile.city}")
    print(f"Resumen: {profile.summary}")
    print(f"Skills: {profile.skills}")
    print(f"Proyectos: {profile.projects}")
    print(f"Educacion: {profile.education}")
    print(f"Roles objetivo: {profile.target_roles}")
    return 0


def _handle_profile_set(args, session: Session, settings, session_factory) -> int:
    profile = upsert_profile(
        session,
        full_name=args.full_name,
        email=args.email,
        phone=args.phone,
        city=args.city,
        summary=args.summary,
        skills=args.skills,
        projects=args.projects,
        education=args.education,
        target_roles=args.target_roles,
    )
    print(f"Perfil guardado: {profile.full_name}")
    return 0


def _handle_offer_add(args, session: Session, settings, session_factory) -> int:
    offer = create_offer(
        session,
        title=args.title,
        company=args.company,
        portal=args.portal,
        location=args.location,
        modality=args.modality,
        salary=args.salary,
        url=args.url,
        description=args.description,
        requirements=args.requirements,
        notes=args.notes,
    )
    profile = get_profile(session)
    offer = refresh_offer_match(session, offer, profile)
    print(f"Oferta registrada con id {offer.id} y score {offer.compatibility_score:.0f}%")
    return 0


def _handle_offer_list(args, session: Session, settings, session_factory) -> int:
    offers = list_offers(session, portal=args.portal)
    if not offers:
        print("No hay ofertas registradas.")
        return 0
    for offer in offers:
        freshness = offer.found_at or offer.created_at
        print(
            f"[{offer.id}] {offer.title} | {offer.company} | {offer.portal} | {offer.status} | "
            f"{offer.compatibility_score:.0f}% | {freshness} | {offer.url}"
        )
    return 0


def _handle_offer_fresh(args, session: Session, settings, session_factory) -> int:
    offers = list_fresh_offers(session, portal=args.portal, hours=args.hours)
    if not offers:
        print("No hay ofertas frescas registradas.")
        return 0
    for offer in offers:
        print(
            f"[{offer.id}] {offer.title} | {offer.company} | {offer.portal} | "
            f"{offer.compatibility_score:.0f}% | {offer.url}"
        )
    return 0


def _handle_offer_show(args, session: Session, settings, session_factory) -> int:
    offer = get_offer_by_id(session, args.id)
    if offer is None:
        print("Oferta no encontrada.")
        return 1
    for key in (
        "id",
        "title",
        "company",
        "portal",
        "location",
        "modality",
        "salary",
        "url",
        "status",
        "compatibility_score",
        "telegram_notified",
        "telegram_notified_at",
        "published_at",
        "found_at",
        "source_id",
        "application_type",
    ):
        print(f"{key}: {getattr(offer, key)}")
    print("match_reason:")
    print(offer.match_reason)
    print("description:")
    print(offer.description)
    print("requirements:")
    print(offer.requirements)
    print("notes:")
    print(offer.notes)
    return 0


def _handle_offer_status(args, session: Session, settings, session_factory) -> int:
    offer = update_offer_status(session, args.id, args.status)
    if offer is None:
        print("Oferta no encontrada.")
        return 1
    print(f"Estado actualizado a: {offer.status}")
    return 0


def _handle_offer_notes(args, session: Session, settings, session_factory) -> int:
    offer = update_offer_notes(session, args.id, args.notes)
    if offer is None:
        print("Oferta no encontrada.")
        return 1
    print("Notas actualizadas.")
    return 0


def _handle_offer_message(args, session: Session, settings, session_factory) -> int:
    offer = get_offer_by_id(session, args.id)
    profile = get_profile(session)
    if offer is None or profile is None:
        print("Se requiere una oferta valida y un perfil cargado.")
        return 1
    print(generate_application_message(profile, offer))
    return 0


def _handle_offer_cv(args, session: Session, settings, session_factory) -> int:
    offer = get_offer_by_id(session, args.id)
    profile = get_profile(session)
    if offer is None or profile is None:
        print("Se requiere una oferta valida y un perfil cargado.")
        return 1
    template_path = settings.templates_dir / "cv_base.docx"
    output_dir = settings.generated_dir / "cvs"
    file_path = generate_cv(output_dir, profile, offer, template_path if template_path.exists() else None)
    register_generated_cv(session, offer, file_path)
    print(f"CV generado en: {file_path}")
    return 0


def _handle_offer_clear(args, session: Session, settings, session_factory) -> int:
    if not args.yes:
        try:
            confirmation = input(
                "Esto borrará ofertas, hashes vistos y notificaciones relacionadas. ¿Continuar? (yes/no) "
            ).strip()
        except EOFError:
            print("Operacion cancelada: se requiere confirmacion interactiva o usar --yes.")
            return 1
        if confirmation.lower() != "yes":
            print("Operacion cancelada.")
            return 1

    result = clear_offers(session, portal=args.portal)
    print(f"Ofertas eliminadas: {result['offers_deleted']}")
    print(f"Hashes eliminados: {result['hashes_deleted']}")
    print(f"Notificaciones eliminadas: {result['notifications_deleted']}")
    print(f"Documentos relacionados eliminados: {result['documents_deleted']}")
    print("Fuentes conservadas: job_search_sources")
    return 0


def _handle_offer_pending_alerts(args, session: Session, settings, session_factory) -> int:
    offers = list_pending_alert_offers(session, threshold=settings.match_threshold, portal=args.portal)
    if not offers:
        print("No hay ofertas pendientes de alerta.")
        return 0
    for offer in offers:
        print(
            f"[{offer.id}] {offer.title} | {offer.company} | {offer.portal} | "
            f"{offer.compatibility_score:.0f}% | {offer.url}"
        )
    return 0


def _handle_discarded_list(args, session: Session, settings, session_factory) -> int:
    limit = args.limit if args.limit and args.limit > 0 else DEFAULT_DISCARDED_LIST_LIMIT
    total = count_discarded_jobs(
        session,
        portal=args.portal,
        target_role=args.target_role,
    )
    records = list_discarded_jobs(
        session,
        portal=args.portal,
        target_role=args.target_role,
        limit=limit,
    )
    if not records:
        print("No hay ofertas descartadas registradas.")
        return 0
    shown = len(records)
    if total > shown:
        print(
            f"Mostrando {shown} de {total} descartadas. Usa --limit para ampliar el listado."
        )
    else:
        print(f"Mostrando {shown} descartadas.")
    for record in records:
        print(
            f"[{record.id}] {record.title} | {record.company} | {record.portal} | "
            f"{record.target_role} | {short_discard_reason(record)} | {record.normalized_url or record.url}"
        )
    return 0


def _handle_discarded_show(args, session: Session, settings, session_factory) -> int:
    record = get_discarded_job_by_id(session, args.id)
    if record is None:
        print("Oferta descartada no encontrada.")
        return 1
    for key in (
        "id",
        "title",
        "company",
        "portal",
        "target_role",
        "location",
        "modality",
        "salary",
        "url",
        "source_url",
        "found_at",
        "seen_count",
        "last_seen_at",
        "compatibility_score",
        "source_id",
        "application_type",
    ):
        print(f"{key}: {getattr(record, key)}")
    print("discard_reasons:")
    print(_format_json_text_list(record.discard_reasons))
    print("detected_keywords:")
    print(_format_json_text_list(record.detected_keywords))
    print("description:")
    print(record.description)
    print("requirements:")
    print(record.requirements)
    print("raw_posted_text:")
    print(record.raw_posted_text)
    return 0


def _handle_discarded_clear(args, session: Session, settings, session_factory) -> int:
    if not args.yes:
        try:
            confirmation = input(
                "Esto borrara solo ofertas descartadas. Continuar? (yes/no) "
            ).strip()
        except EOFError:
            print("Operacion cancelada: se requiere confirmacion interactiva o usar --yes.")
            return 1
        if confirmation.lower() != "yes":
            print("Operacion cancelada.")
            return 1
    deleted = clear_discarded_jobs(session, portal=args.portal, target_role=args.target_role)
    print(f"Descartadas eliminadas: {deleted}")
    print("Ofertas reales conservadas: job_offers")
    print("Fuentes conservadas: job_search_sources")
    return 0


def _handle_discarded_reprocess(args, session: Session, settings, session_factory) -> int:
    if args.id is None and not args.portal and not args.target_role:
        print("Debes indicar al menos una opcion: --id, --portal o --target-role.")
        return 1
    profile = get_profile(session)
    results = reprocess_discarded_jobs(
        session,
        create_offer=create_offer,
        refresh_offer_match=refresh_offer_match,
        profile=profile,
        discarded_job_id=args.id,
        portal=args.portal,
        target_role=args.target_role,
    )
    if not results:
        print("No se encontraron ofertas descartadas para reprocesar.")
        return 0
    accepted = 0
    still_discarded = 0
    for item in results:
        if item.accepted:
            accepted += 1
            print(f"[{item.discarded_job_id}] aceptada -> job_offer {item.offer_id} | {item.title}")
        else:
            still_discarded += 1
            reason_text = "; ".join(item.reasons) if item.reasons else "sin razon registrada"
            print(f"[{item.discarded_job_id}] sigue descartada | {item.title} | {reason_text}")
    print(f"Reprocesadas: {len(results)} | aceptadas: {accepted} | descartadas: {still_discarded}")
    return 0


def _handle_discarded_export(args, session: Session, settings, session_factory) -> int:
    file_path = Path(args.file)
    try:
        exported = export_discarded_jobs(
            session,
            file_path=file_path,
            portal=args.portal,
            target_role=args.target_role,
        )
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"Descartadas exportadas: {exported}")
    print(f"Archivo: {file_path}")
    return 0


def _handle_sources_add(args, session: Session, settings, session_factory) -> int:
    portal = args.portal.strip().lower()
    if portal not in list_supported_portals():
        print(f"Portal no soportado: {portal}")
        print("Disponibles: " + ", ".join(list_supported_portals()))
        return 1
    if args.target_role not in get_available_targets():
        print(f"Target no soportado: {args.target_role}")
        print("Disponibles: " + ", ".join(get_available_targets()))
        return 1
    try:
        source = add_source(
            session,
            portal=portal,
            target_role=args.target_role,
            search_url=args.url,
            keywords=args.keywords,
            location=args.location,
            interval_minutes=args.interval,
            min_interval_minutes=settings.min_monitor_interval_minutes,
        )
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"Fuente agregada con id {source.id}: {source.portal} -> {source.search_url}")
    return 0


def _handle_sources_list(args, session: Session, settings, session_factory) -> int:
    sources = list_sources(session)
    if not sources:
        print("No hay fuentes configuradas.")
        return 0
    for source in sources:
        print(
            f"[{source.id}] {source.portal} | target={source.target_role} | enabled={source.enabled} | "
            f"interval={source.interval_minutes}m | failure_count={source.failure_count} | "
            f"paused_until={source.paused_until} | last_checked={source.last_checked_at} | url={source.search_url}"
        )
        if source.last_error:
            print(f"    ultimo_error: {source.last_error}")
    return 0


def _handle_sources_enable(args, session: Session, settings, session_factory) -> int:
    source = set_source_enabled(session, args.id, True)
    if source is None:
        print("Fuente no encontrada.")
        return 1
    print(f"Fuente activada: {source.portal} ({source.id})")
    return 0


def _handle_sources_disable(args, session: Session, settings, session_factory) -> int:
    source = set_source_enabled(session, args.id, False)
    if source is None:
        print("Fuente no encontrada.")
        return 1
    print(f"Fuente desactivada: {source.portal} ({source.id})")
    return 0


def _handle_sources_test(args, session: Session, settings, session_factory) -> int:
    source = get_source_by_id(session, args.id)
    if source is None:
        print("Fuente no encontrada.")
        return 1
    result = test_source(settings, source)
    if args.debug_html:
        html_path, meta_path = _write_source_debug_files(source, result)
        print(f"Debug HTML: {html_path}")
        print(f"Debug meta: {meta_path}")
    print(f"Portal: {source.portal}")
    print(f"URL: {source.search_url}")
    if result.error:
        print(f"Error: {result.error}")
        return 1
    print(f"Ofertas detectadas: {len(result.offers)}")
    for item in result.offers[:5]:
        print(f"- {item.title} | {item.company} | {item.url}")
        preview = (item.description or item.requirements or "").replace("\n", " ").strip()
        if preview:
            print(f"  descripcion: {preview[:180]}")
    if args.show_discarded and result.discarded:
        print(f"Ofertas descartadas: {len(result.discarded)}")
        for discarded in result.discarded[:5]:
            reason_text = "; ".join(discarded.reasons) if discarded.reasons else "sin razon registrada"
            keywords_text = ", ".join(discarded.detected_keywords) if discarded.detected_keywords else "sin keywords"
            score_text = discarded.preliminary_score if discarded.preliminary_score is not None else "n/a"
            print(
                f"- {discarded.job.title} | {discarded.job.company} | target={source.target_role} | "
                f"razones={reason_text} | keywords={keywords_text} | score={score_text} | "
                f"url={discarded.job.url}"
            )
    return 0


def _write_source_debug_files(source, result):
    debug_dir = Path("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    html_path = debug_dir / f"source_{source.id}_{source.portal}.html"
    meta_path = debug_dir / f"source_{source.id}_{source.portal}_meta.txt"
    snapshot = result.debug_snapshot
    html_content = snapshot.html if snapshot is not None else ""
    meta_lines = [
        f"requested_url: {snapshot.requested_url if snapshot is not None else source.search_url}",
        f"status_code: {snapshot.status_code if snapshot is not None else 'unknown'}",
        f"final_url: {snapshot.final_url if snapshot is not None else source.search_url}",
        f"content_type: {snapshot.content_type if snapshot is not None else 'unknown'}",
        f"html_size: {len(html_content)}",
        f"block_reason: {snapshot.block_reason if snapshot is not None and snapshot.block_reason else (result.error or 'none')}",
        "html_preview:",
        (html_content[:500] if html_content else ""),
    ]
    html_path.write_text(html_content, encoding="utf-8")
    meta_path.write_text("\n".join(meta_lines), encoding="utf-8")
    return html_path, meta_path


def _format_json_text_list(raw_value: str) -> str:
    values = parse_text_list(raw_value)
    if not values:
        return "[]"
    return json.dumps(values, ensure_ascii=False, indent=2)


def _handle_sources_update_interval(args, session: Session, settings, session_factory) -> int:
    if bool(args.id) == bool(args.portal):
        print("Debes indicar exactamente una opcion: --id o --portal.")
        return 1
    try:
        if args.id:
            source = update_source_interval(
                session,
                args.id,
                interval_minutes=args.interval,
                min_interval_minutes=settings.min_monitor_interval_minutes,
            )
            if source is None:
                print("Fuente no encontrada.")
                return 1
            print(f"Fuente {source.id} actualizada a intervalo {source.interval_minutes} minutos.")
            return 0

        sources = update_portal_source_intervals(
            session,
            args.portal,
            interval_minutes=args.interval,
            min_interval_minutes=settings.min_monitor_interval_minutes,
            enabled_only=True,
        )
    except ValueError as exc:
        print(str(exc))
        return 1

    if not sources:
        print(f"No se encontraron fuentes activas para el portal {args.portal.strip().lower()}.")
        return 1
    print(
        f"Fuentes de {args.portal.strip().lower()} actualizadas a intervalo {args.interval} minutos: {len(sources)} fuentes."
    )
    return 0


def _handle_sources_unpause(args, session: Session, settings, session_factory) -> int:
    if bool(args.id) == bool(args.portal):
        print("Debes indicar exactamente una opcion: --id o --portal.")
        return 1

    if args.id:
        source = unpause_source_by_id(session, args.id)
        if source is None:
            print("Fuente no encontrada.")
            return 1
        print(f"Fuente {source.id} reanudada.")
        return 0

    sources = unpause_sources_by_portal(session, args.portal)
    if not sources:
        print(f"No se encontraron fuentes para el portal {args.portal.strip().lower()}.")
        return 1
    print(f"Fuentes de {args.portal.strip().lower()} reanudadas: {len(sources)} fuentes.")
    return 0


def _handle_sources_disable_blocked(args, session: Session, settings, session_factory) -> int:
    sources = disable_blocked_sources(session)
    if not sources:
        print("No hay fuentes bloqueadas para desactivar.")
        return 0
    print(f"Fuentes bloqueadas desactivadas: {len(sources)}")
    return 0


def _handle_selenium_test(args, session: Session, settings, session_factory) -> int:
    portal = _normalize_selenium_portal(args.portal)
    try:
        search_url = _resolve_selenium_test_url(args, portal)
    except ValueError as exc:
        print(str(exc))
        return 1
    source = JobSearchSource(
        portal=portal,
        target_role=args.target_role,
        search_url=search_url,
        keywords=(getattr(args, "keyword", None) or "").strip(),
        location=(getattr(args, "location", None) or "").strip(),
        enabled=True,
        interval_minutes=max(30, settings.min_monitor_interval_minutes),
    )
    result = test_source(settings, source)
    print(f"Portal: {portal}")
    print(f"URL: {source.search_url}")
    if result.error:
        print(f"Error: {result.error}")
        return 1
    print(f"Ofertas detectadas: {len(result.offers)}")
    for item in result.offers[:10]:
        print(f"- {item.title} | {item.company} | {item.location} | {item.url}")
        preview = (item.description or item.requirements or "").replace("\n", " ").strip()
        if preview:
            print(f"  descripcion: {preview[:180]}")
    if result.discarded:
        print(f"Ofertas descartadas por relevancia: {len(result.discarded)}")
        for discarded in result.discarded[:5]:
            reason_text = "; ".join(discarded.reasons) if discarded.reasons else "sin razon registrada"
            print(f"- Descartada: {discarded.job.title} | razon: {reason_text} | {discarded.job.url}")
    return 0


def _normalize_selenium_portal(portal: str) -> str:
    normalized = portal.strip().lower()
    if normalized in {"indeed", "linkedin"}:
        return f"{normalized}_selenium"
    return normalized


def _resolve_selenium_test_url(args, portal: str) -> str:
    explicit_url = (getattr(args, "url", None) or "").strip()
    if explicit_url:
        return explicit_url
    if portal != "linkedin_selenium":
        raise ValueError("Debes indicar --url para este portal.")
    return build_linkedin_jobs_url(
        getattr(args, "keyword", "") or "",
        getattr(args, "location", "") or "",
        date_posted=getattr(args, "date_posted", "24h") or "24h",
        experience_levels=getattr(args, "experience_levels", None),
        workplace_types=getattr(args, "workplace_types", None),
    )


def _handle_linkedin_login_profile(args, session: Session, settings, session_factory) -> int:
    driver = None
    try:
        driver = _build_linkedin_profile_driver(settings)
        _navigate_linkedin_profile_driver(driver, LINKEDIN_HOME_URL)
        print(LINKEDIN_LOGIN_PROFILE_MESSAGE)
        _wait_for_linkedin_profile_browser_close(driver)
        return 0
    except Exception as exc:
        print(str(exc))
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def _handle_linkedin_profile_info(args, session: Session, settings, session_factory) -> int:
    user_data_dir = SeleniumJobScraper._expand_chrome_setting(
        getattr(settings, "selenium_user_data_dir", "")
    )
    profile_directory = str(getattr(settings, "selenium_profile_directory", "") or "").strip()
    profile_path = Path(user_data_dir) / profile_directory if user_data_dir and profile_directory else Path(user_data_dir)
    print(f"JOBOPS_SELENIUM_USER_DATA_DIR: {user_data_dir}")
    print(f"JOBOPS_SELENIUM_PROFILE_DIRECTORY: {profile_directory}")
    print(f"carpeta existe: {profile_path.exists()}")
    print(f"JOBOPS_SELENIUM_HEADLESS: {settings.selenium_headless}")
    return 0


def _build_linkedin_profile_driver(settings):
    scraper = SeleniumJobScraper(settings, log_selenium=False)
    return scraper._build_driver()


def _navigate_linkedin_profile_driver(driver, url: str) -> None:
    driver.get(url)


def _wait_for_linkedin_profile_browser_close(driver, *, poll_seconds: float = 1.0) -> None:
    while True:
        try:
            handles = driver.window_handles
        except Exception:
            return
        if not handles:
            return
        time.sleep(poll_seconds)


def _handle_monitor_fresh(args, session: Session, settings, session_factory) -> int:
    for line in run_fresh_monitor(session, settings, force_all=True, notify_pending=args.notify_pending):
        print(line)
    return 0


def _handle_monitor_watch(args, session: Session, settings, session_factory) -> int:
    if args.interval < settings.min_monitor_interval_minutes:
        print(
            f"El intervalo minimo permitido es de {settings.min_monitor_interval_minutes} minutos."
        )
        return 1
    print("Iniciando monitor continuo. Usa Ctrl+C para detener.")
    try:
        while True:
            with session_factory() as watch_session:
                for line in run_fresh_monitor(watch_session, settings, force_all=False, notify_pending=True):
                    print(line)
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        print("Monitor detenido por el usuario.")
        return 0


def _handle_scan_daily(args, session: Session, settings, session_factory) -> int:
    for line in run_daily_scan(session, settings):
        print(line)
    return 0


def _handle_send_summary(args, session: Session, settings, session_factory) -> int:
    offers = sorted(list_offers(session), key=lambda item: item.compatibility_score, reverse=True)
    if not offers:
        print("No hay ofertas para resumir.")
        return 1
    top = offers[0]
    try:
        sent, message = send_job_alert(settings, top)
        print(message)
        if not sent:
            print(format_job_alert(top))
    except Exception as exc:
        print(f"No se pudo enviar el resumen: {exc}")
        print(format_job_alert(top))
        return 1
    return 0


def _handle_notifications_retry_pending(args, session: Session, settings, session_factory) -> int:
    logs = retry_pending_alerts(session, settings, portal=args.portal)
    for line in logs:
        print(line)
    return 0


def _handle_resume_import(args, session: Session, settings, session_factory) -> int:
    file_path = Path(args.file)
    try:
        profile = read_resume_file(file_path)
        saved_path = save_resume_profile(profile, DEFAULT_RESUME_PROFILE_PATH)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1

    print(f"Hoja de vida importada en: {saved_path}")
    print(f"Nombre detectado: {profile.full_name or 'No detectado'}")
    print(f"Email: {profile.email or 'No detectado'}")
    print(f"Telefono: {profile.phone or 'No detectado'}")
    print(f"LinkedIn: {profile.linkedin or 'No detectado'}")
    print(f"Habilidades tecnicas: {len(profile.technical_skills)}")
    print(f"Experiencias: {len(profile.experiences)}")
    print(f"Proyectos: {len(profile.projects)}")
    print(f"Educacion: {len(profile.education)}")
    return 0


def _handle_resume_show(args, session: Session, settings, session_factory) -> int:
    try:
        profile = load_resume_profile(DEFAULT_RESUME_PROFILE_PATH)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print(f"Nombre: {profile.full_name}")
    print(f"Ubicacion: {profile.location}")
    print(f"Telefono: {profile.phone}")
    print(f"Email: {profile.email}")
    print(f"LinkedIn: {profile.linkedin}")
    print(f"GitHub: {profile.github}")
    print(f"Portafolio: {profile.portfolio}")
    print(f"Resumen profesional: {profile.professional_summary}")
    print(f"Habilidades tecnicas: {', '.join(profile.technical_skills)}")
    print(f"Habilidades blandas: {', '.join(profile.soft_skills)}")
    print("Experiencias:")
    for item in profile.experiences:
        print(f"- {item.role} | {item.company} | {item.location} | {item.start_date} - {item.end_date}")
    print("Educacion:")
    for item in profile.education:
        print(f"- {item}")
    print("Certificaciones:")
    for item in profile.certifications:
        print(f"- {item}")
    print("Idiomas:")
    for item in profile.languages:
        print(f"- {item}")
    return 0


def _handle_resume_targets(args, session: Session, settings, session_factory) -> int:
    for target in get_available_targets():
        print(target)
    return 0


def _handle_resume_generate_ats(args, session: Session, settings, session_factory) -> int:
    try:
        profile = load_resume_profile(DEFAULT_RESUME_PROFILE_PATH)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    if args.target not in get_available_targets():
        print(f"Target no soportado: {args.target}")
        print("Disponibles: " + ", ".join(get_available_targets()))
        return 1

    job_offer = None
    if args.job_id is not None:
        job_offer = get_offer_by_id(session, args.job_id)
        if job_offer is None:
            print(f"Oferta no encontrada para job-id {args.job_id}.")
            return 1

    file_name = build_ats_filename(profile, args.target, job_offer)
    output_path = settings.generated_dir / "cvs" / file_name
    build_ats_resume(profile, args.target, output_path, job_offer)
    print(f"CV ATS generado en: {output_path}")
    return 0
