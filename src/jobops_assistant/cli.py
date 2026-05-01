from __future__ import annotations

import argparse
from pathlib import Path
import time

from sqlalchemy.orm import Session

from .ats_resume_builder import build_ats_filename, build_ats_resume, get_available_targets
from .cv_generator import generate_cv, register_generated_cv
from .database import create_session_factory, create_sqlite_engine, init_db
from .freshness_monitor import run_fresh_monitor
from .job_service import (
    create_offer,
    get_offer_by_id,
    list_fresh_offers,
    list_offers,
    refresh_offer_match,
    update_offer_notes,
    update_offer_status,
)
from .message_generator import generate_application_message
from .profile_service import get_profile, upsert_profile
from .resume_profile_service import DEFAULT_RESUME_PROFILE_PATH, load_resume_profile, save_resume_profile
from .resume_reader import read_resume_file
from .scrapers.registry import list_supported_portals
from .search_sources import add_source, get_source_by_id, list_sources, set_source_enabled, test_source
from .settings import load_settings
from .telegram_notifier import format_job_alert, send_job_alert
from .workflows import run_daily_scan


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
    sources_test.set_defaults(handler=_handle_sources_test)

    monitor_parser = subparsers.add_parser("monitor", help="Monitorea ofertas frescas")
    monitor_sub = monitor_parser.add_subparsers(dest="monitor_command", required=True)

    monitor_fresh = monitor_sub.add_parser("fresh", help="Ejecuta el monitor una vez")
    monitor_fresh.set_defaults(handler=_handle_monitor_fresh)

    monitor_watch = monitor_sub.add_parser("watch", help="Ejecuta el monitor en bucle")
    monitor_watch.add_argument("--interval", type=int, default=15)
    monitor_watch.set_defaults(handler=_handle_monitor_watch)

    scan_parser = subparsers.add_parser("scan-daily", help="Ejecuta flujo diario basado en Gmail")
    scan_parser.set_defaults(handler=_handle_scan_daily)

    summary_parser = subparsers.add_parser("send-summary", help="Envia resumen de la oferta mas relevante")
    summary_parser.set_defaults(handler=_handle_send_summary)

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
        "published_at",
        "found_at",
        "source_id",
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
            f"interval={source.interval_minutes}m | last_checked={source.last_checked_at} | url={source.search_url}"
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
    return 0


def _handle_monitor_fresh(args, session: Session, settings, session_factory) -> int:
    for line in run_fresh_monitor(session, settings, force_all=True):
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
                for line in run_fresh_monitor(watch_session, settings, force_all=False):
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
