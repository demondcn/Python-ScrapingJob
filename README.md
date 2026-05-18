# JobOps Personal Assistant

Aplicacion personal en Python para centralizar ofertas laborales, calificarlas segun el perfil del candidato, generar mensajes de postulacion, producir CVs en DOCX y llevar seguimiento del proceso manualmente.

## Objetivo

Este proyecto ayuda a organizar una busqueda laboral tecnica orientada a roles junior como:

- DevOps Trainee
- Soporte de Aplicaciones
- Infraestructura Junior
- Cloud Support
- QA Junior
- Backend Junior
- Frontend Junior
- Fullstack Junior

No autoaplica, no usa bots para operar cuentas reales y no hace scraping agresivo.

## Funcionalidades del MVP

- Perfil del candidato en SQLite
- CRUD de ofertas laborales
- Deteccion de duplicados por URL
- Puntaje de compatibilidad de 0 a 100
- Mensajes de postulacion personalizados
- Generacion de CV en formato DOCX
- Monitoreo responsable de ofertas frescas desde URLs publicas
- Integracion opcional con Telegram
- Flujo diario preparado para integracion opcional con Gmail

## Estructura

```text
.
├── config/
├── data/
├── generated/
│   └── cvs/
├── src/
│   └── jobops_assistant/
│       └── scrapers/
├── templates/
├── tests/
├── .env.example
├── main.py
└── requirements.txt
```

## Instalacion

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py init-db
```

## Configuracion

Variables principales en `.env`:

- `JOBOPS_DB_PATH`: ruta del archivo SQLite
- `JOBOPS_MATCH_THRESHOLD`: minimo para alertas sugeridas
- `JOBOPS_SCRAPER_TIMEOUT`: timeout HTTP por fuente
- `JOBOPS_SCRAPER_USER_AGENT`: user agent del monitor responsable
- `JOBOPS_MAX_RESULTS_PER_SOURCE`: maximo de resultados por fuente
- `JOBOPS_MIN_MONITOR_INTERVAL_MINUTES`: intervalo minimo entre revisiones
- `JOBOPS_TIMEZONE`: zona horaria para mensajes y fechas
- `JOBOPS_TELEGRAM_DIGEST_LIMIT`: maximo de ofertas por digest; `0` envia todas las ofertas pendientes
- `JOBOPS_TELEGRAM_MAX_MESSAGE_CHARS`: limite aproximado por parte del digest
- `JOBOPS_NOTIFY_AFTER_EACH_SOURCE`: si esta en `true`, envia Telegram despues de cada fuente y no espera al final del ciclo
- `JOBOPS_ENABLE_SELENIUM`: activa scrapers opcionales con Selenium
- `JOBOPS_SELENIUM_HEADLESS`: ejecuta el navegador Selenium sin ventana visible
- `JOBOPS_SELENIUM_PAGE_LOAD_TIMEOUT`: timeout de carga de pagina Selenium
- `JOBOPS_SELENIUM_SCROLL_PAUSE`: pausa entre scrolls Selenium
- `JOBOPS_SELENIUM_MAX_SCROLLS`: cantidad maxima de scrolls Selenium
- `JOBOPS_SELENIUM_USER_DATA_DIR`: perfil local de Chrome que Selenium debe reutilizar
- `JOBOPS_SELENIUM_PROFILE_DIRECTORY`: carpeta del perfil de Chrome, por ejemplo `Default`
- `JOBOPS_LINKEDIN_FETCH_DETAILS`: intenta abrir cada oferta publica de LinkedIn para leer `div.description__text` (default `false`)
- `JOBOPS_LINKEDIN_ONLY_EASY_APPLY`: si esta en `true`, `linkedin_selenium` solo guarda y notifica vacantes con solicitud sencilla
- `TELEGRAM_BOT_TOKEN`: token del bot
- `TELEGRAM_CHAT_ID`: chat id destino
- `GMAIL_EMAIL`: correo para IMAP
- `GMAIL_APP_PASSWORD`: app password de Gmail

## Uso rapido

Crear o actualizar perfil:

```powershell
python main.py profile set --full-name "Cris Perez" --email "cris@example.com" --phone "3000000000" --city "Bogota" --summary "Tecnologo en desarrollo de software con interes en DevOps" --skills "Python,SQL,Git,Linux,Docker" --projects "Portafolio web; Automatizaciones locales" --education "Tecnologo en Desarrollo de Software" --target-roles "DevOps Trainee,Soporte de Aplicaciones,Backend Junior"
```

Agregar oferta manual:

```powershell
python main.py offer add --title "Soporte de Aplicaciones Junior" --company "ABC Tecnologia" --portal "Computrabajo" --location "Bogota" --modality "Hibrido" --url "https://ejemplo.com/oferta" --description "Soporte a usuarios, SQL, tickets, aplicaciones web"
```

Listar ofertas:

```powershell
python main.py offer list
```

Listar ofertas por portal:

```powershell
python main.py offer list --portal linkedin
```

Ver ofertas frescas:

```powershell
python main.py offer fresh
```

Ver detalle:

```powershell
python main.py offer show --id 1
```

Cambiar estado:

```powershell
python main.py offer update-status --id 1 --status applied
```

Generar mensaje:

```powershell
python main.py offer generate-message --id 1
```

Generar CV:

```powershell
python main.py offer generate-cv --id 1
```

Limpiar solo ofertas guardadas:

```powershell
python main.py offer clear
python main.py offer clear --yes
python main.py offer clear --portal computrabajo --yes
```

Ver ofertas pendientes de alerta:

```powershell
python main.py offer pending-alerts
```

Ejecutar escaneo diario preparado:

```powershell
python main.py scan-daily
```

## Telegram

1. Crea un bot con BotFather.
2. Guarda `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`.
3. Usa:

```powershell
python main.py send-summary
```

Si faltan credenciales, la aplicacion no falla: solo mostrara una advertencia.

Las alertas de Telegram muestran la fecha/hora de publicacion cuando el portal la permite y siempre incluyen la fecha/hora de deteccion por JobOps.
El monitor usa modo digest por ciclo: no manda un mensaje por cada oferta, sino un resumen agrupado de las ofertas notificables encontradas en el ciclo. Si el resumen es muy largo, lo divide en partes automaticamente. `JOBOPS_TELEGRAM_DIGEST_LIMIT=0` envia todas las ofertas pendientes; si se configura con un numero mayor que cero, envia como maximo esa cantidad.

## Gmail

La integracion esta preparada para usarse con IMAP y App Password de Gmail, pero el lector actual es conservador y devuelve resultados vacios hasta que se configure una extraccion real de correos.

## Generacion de CV ATS desde HV base

El proyecto tambien puede importar una hoja de vida base en `DOCX` o `PDF`, extraer su contenido y generar versiones ATS adaptadas a un perfil objetivo o a una oferta concreta.

Comandos:

```powershell
python main.py resume import --file "HV Cristian Stiven Guerrero Andrade.docx"
python main.py resume targets
python main.py resume show
python main.py resume generate-ats --target devops_trainee
python main.py resume generate-ats --target soporte_aplicaciones
python main.py resume generate-ats --target infraestructura_junior
python main.py resume generate-ats --target backend_junior
python main.py resume generate-ats --target frontend_junior
python main.py resume generate-ats --target fullstack_junior
python main.py resume generate-ats --target soporte_aplicaciones --job-id 1
```

Perfiles objetivo disponibles:

- `devops_trainee`
- `soporte_aplicaciones`
- `infraestructura_junior`
- `cloud_support`
- `qa_junior`
- `backend_junior`
- `frontend_junior`
- `fullstack_junior`

Comportamiento del generador ATS:

- Genera un DOCX simple, en una sola columna y sin tablas complejas
- No incluye foto, referencias, hobbies ni barras de habilidad
- No inventa experiencia ni datos que no existan en la HV base
- Reordena habilidades y selecciona bullets relevantes segun el perfil objetivo
- Si se usa `--job-id`, refuerza keywords segun la oferta guardada en SQLite
- El resultado debe revisarse manualmente antes de enviarlo

Persistencia del perfil base:

- El perfil importado se guarda en `data/resume_profile.json`
- Los CV ATS generados se guardan en `generated/cvs/`

## Monitoreo de ofertas frescas por scraping responsable

La app no autoaplica, no inicia sesion, no evade captchas, no usa cuentas reales, no usa proxies y no intenta saltarse bloqueos `403`, `429` o pantallas de login. Solo revisa URLs publicas de busqueda que configuras manualmente en cada portal.

Flujo recomendado:

1. Crear una busqueda manual en el portal con filtros como `junior`, `trainee`, `ultimas 24 horas`, `Bogota`, `remoto`, `hibrido`, etc.
2. Copiar la URL publica resultante.
3. Registrar la fuente en JobOps.
4. Ajustar el intervalo de revision si hace falta.
5. Probar la fuente y luego ejecutar el monitor.

Portales soportados:

- `linkedin`
- `computrabajo`
- `elempleo`
- `indeed`
- `magneto`
- `torre`
- `getonboard`
- `sena`

Comandos:

```powershell
python main.py sources add --portal linkedin --target-role devops_trainee --url "https://www.linkedin.com/jobs/search/?keywords=DevOps%20Trainee&location=Colombia&f_TPR=r86400" --interval 15
python main.py sources add --portal computrabajo --target-role soporte_aplicaciones --url "URL_DE_COMPUTRABAJO_ORDENADA_POR_FECHA" --interval 15
python main.py sources add --portal indeed_selenium --target-role backend_junior --url "URL_DE_INDEED" --interval 30
python main.py sources add --portal linkedin_selenium --target-role devops_trainee --url "URL_DE_LINKEDIN" --interval 30
python main.py sources list
python main.py sources update-interval --id 1 --interval 10
python main.py sources update-interval --portal computrabajo --interval 10
python main.py sources unpause --id 14
python main.py sources unpause --portal elempleo
python main.py sources disable-blocked
python main.py sources test --id 1
python main.py sources test --id 14 --debug-html
python main.py sources test --id 14 --show-discarded
python main.py selenium test --portal indeed --url "URL_DE_INDEED" --target-role backend_junior
python main.py selenium test --portal linkedin --url "URL_DE_LINKEDIN" --target-role devops_trainee
python main.py selenium test --portal linkedin --keyword "DevOps Trainee" --location "Colombia" --date-posted 24h --experience-level entry_level --workplace remote --workplace hybrid --target-role devops_trainee
python main.py monitor fresh
python main.py monitor fresh --notify-pending
python main.py monitor watch --interval 15
python main.py offer fresh
python main.py offer list --portal linkedin
python main.py offer pending-alerts
python main.py notifications retry-pending
```

Comportamiento:

- Usa `requests + BeautifulSoup` sobre HTML publico cuando es posible
- Si un portal bloquea, pide captcha o exige login, registra el error y continua con las demas fuentes
- Si una fuente falla repetidamente por captcha, login, `403`, `429` o bloqueo publico, acumula `failure_count` y se pausa automaticamente por 24 horas al tercer fallo
- Las fuentes pausadas no se revisan en `watch` mientras `paused_until` siga en el futuro, y pueden reanudarse con `sources unpause`
- `sources disable-blocked` desactiva las fuentes que ya acumularon bloqueos repetidos
- `sources test --debug-html` guarda el HTML real y metadatos de la respuesta en `debug/` para depurar falsos positivos de captcha o bloqueo
- Antes de guardar una oferta, JobOps valida si realmente coincide con el `target-role` de la fuente; si no coincide, la descarta y no la notifica
- `sources test --show-discarded` muestra ejemplos de ofertas descartadas con razones, keywords detectadas y score preliminar
- Evita duplicados por URL normalizada y hash
- Guarda ofertas nuevas en SQLite
- Calcula compatibilidad con el matcher existente
- Si la oferta supera `JOBOPS_MATCH_THRESHOLD`, la agrega al digest del ciclo y envia un resumen agrupado por Telegram con link oficial y comandos sugeridos para CV ATS y cambio de estado
- Con `JOBOPS_NOTIFY_AFTER_EACH_SOURCE=true`, cada fuente envia su propio digest inmediatamente al terminar; el digest final solo procesa pendientes que no se enviaron en esos envios por fuente.
- `Duplicado` significa que la oferta ya estaba guardada; no que la alerta quede descartada
- Si una oferta supera el umbral pero no fue enviada por Telegram, queda como pendiente de alerta y puede reintentarse despues
- `offer clear` borra ofertas, hashes vistos y registros relacionados, pero conserva las fuentes configuradas

## Scraping opcional con Selenium

Selenium es opcional y esta desactivado por defecto. Sirve para probar paginas publicas que cargan resultados de forma dinamica y que no entregan HTML util con `requests`, especialmente Indeed y LinkedIn.

Activacion en `.env`:

```powershell
JOBOPS_ENABLE_SELENIUM=true
JOBOPS_SELENIUM_HEADLESS=false
JOBOPS_SELENIUM_PAGE_LOAD_TIMEOUT=30
JOBOPS_SELENIUM_SCROLL_PAUSE=3
JOBOPS_SELENIUM_MAX_SCROLLS=5
JOBOPS_SELENIUM_USER_DATA_DIR=%LOCALAPPDATA%/Google/Chrome/User Data
JOBOPS_SELENIUM_PROFILE_DIRECTORY=Default
JOBOPS_LINKEDIN_FETCH_DETAILS=false
JOBOPS_LINKEDIN_ONLY_EASY_APPLY=true
```

Comandos de prueba:

```powershell
python main.py selenium test --portal indeed --url "URL_DE_INDEED" --target-role backend_junior
python main.py selenium test --portal linkedin --url "URL_DE_LINKEDIN" --target-role devops_trainee
python main.py selenium test --portal linkedin --keyword "DevOps Trainee" --location "Colombia" --date-posted 24h --experience-level entry_level --workplace remote --workplace hybrid --target-role devops_trainee
python main.py selenium test --portal linkedin --keyword "Junior Backend" --location "Colombia" --date-posted 24h --experience-level entry_level --target-role backend_junior
```

Fuentes persistentes:

```powershell
python main.py sources add --portal indeed_selenium --target-role backend_junior --url "URL_DE_INDEED" --interval 30
python main.py sources add --portal linkedin_selenium --target-role devops_trainee --url "URL_GENERADA" --interval 30
```

Reglas de uso:

- Solo usa paginas publicas.
- No automatiza login, no usa proxies y no resuelve captchas; si configuras un perfil de Chrome, reutiliza la sesion/cookies locales de ese perfil.
- Para reutilizar una sesion ya abierta en Chrome, ejecuta Selenium con `JOBOPS_SELENIUM_HEADLESS=false`, `JOBOPS_SELENIUM_USER_DATA_DIR` apuntando al `User Data` local de Chrome y `JOBOPS_SELENIUM_PROFILE_DIRECTORY=Default` o el perfil que corresponda.
- LinkedIn usa Selenium para abrir la busqueda publica, hacer scroll limitado, pulsar el boton publico de mas resultados cuando aparece y parsear cards `div.base-card` con BeautifulSoup.
- Con `JOBOPS_LINKEDIN_ONLY_EASY_APPLY=true`, `linkedin_selenium` descarta las ofertas que no muestren "Solicitud sencilla", "Solicitud simple", "Solicitar facilmente" o "Easy Apply"; esas ofertas van a `discarded_jobs` con la razon `no es solicitud sencilla de LinkedIn`.
- Los filtros del constructor de URL de LinkedIn mapean `24h` a `f_TPR=r86400`, `entry_level` a `f_E=2`, y `remote` + `hybrid` a `f_WT=2,3`.
- Si aparece Security Check, captcha, login, authwall, checkpoint, access denied o forbidden, registra el bloqueo y omite la fuente.
- Puede ser mas lento que los scrapers con `requests`; para fuentes Selenium se recomienda intervalo minimo de 30 minutos.
- Si Indeed o LinkedIn bloquean la lectura publica, usa alertas por correo del portal y el Gmail Reader como alternativa.

Ejemplo de alerta Telegram:

- Cargo, empresa, portal y ubicacion
- Compatibilidad y motivo de coincidencia
- Link oficial para aplicar manualmente
- Comando `python main.py resume generate-ats --target ... --job-id ...`
- Comando `python main.py offer update-status --id ... --status applied`

## Auditoria de ofertas descartadas

Las ofertas descartadas no se guardan como ofertas reales en `job_offers`. Se guardan aparte en `discarded_jobs` para depuracion y para revisar si el filtro esta siendo demasiado estricto.

Esto permite:

- revisar despues que datos tenia cada oferta descartada
- ver la razon exacta del descarte
- inspeccionar keywords detectadas y score preliminar
- reprocesar descartadas con el matcher actual

Comandos principales:

```powershell
python main.py discarded list
python main.py discarded list --portal magneto
python main.py discarded list --target-role frontend_junior
python main.py discarded list --portal elempleo --limit 50
python main.py discarded show --id 1
python main.py discarded clear --yes
python main.py discarded clear --portal magneto --yes
python main.py discarded reprocess --id 1
python main.py discarded export --file descartadas.csv
python main.py discarded export --file descartadas.json --portal magneto
```

Comportamiento:

- `discarded list` muestra por defecto hasta 20 resultados, ordenados por la descartada mas reciente; usa `--limit` para ampliar el listado
- Si `monitor fresh` muestra `descartadas=N`, esas ofertas quedan disponibles en `discarded_jobs`
- `discarded list` permite verlas sin contaminar `job_offers`
- `discarded show` muestra el detalle completo para ajustar reglas si hace falta
- Si una descartada luego deja de ser descartada, puede entrar como `job_offer` normal
- `discarded reprocess` vuelve a evaluarla con el matcher actual y, si ya aplica, la mueve a `job_offers`

## Roadmap

- Lectura real de Gmail
- Parser de ofertas desde HTML/correo
- Dashboard web
- Reportes semanales
- Docker y CI
