#!/usr/bin/env bash
# shellcheck shell=bash
set -euo pipefail

# Phase 6a - Curl guide
# Audiencia: debugging interno. NO commitear valores reales en las env vars.
# Uso:
#   ./phase-6a.sh                    # corre todas las secciones
#   ./phase-6a.sh section_2          # corre solo una seccion
#   ./phase-6a.sh --list             # lista secciones disponibles

: "${BASE_URL:=http://localhost:8000}"
: "${INTERNAL_SECRET:=}"
: "${TEST_USER_ID:=}"
: "${AUTH_HEADER_NAME:=X-Shortcut-Token}"
: "${APP_USER_ID:=}"
: "${DATABASE_URL:=postgresql://finance:finance@localhost:5432/finance}"
: "${PSQL_URL:=${DATABASE_URL/postgresql+asyncpg/postgresql}}"
: "${REDIS_URL:=redis://localhost:6379/0}"
: "${QUERY_TZ:=America/Costa_Rica}"
: "${TELEGRAM_MODE:=polling}"
: "${TELEGRAM_WEBHOOK_SECRET:=}"
: "${REDIS_CONTAINER:=}"

if [[ -z "${APP_USER_ID}" && "${AUTH_HEADER_NAME}" == "X-User-Id" ]]; then
    APP_USER_ID="${INTERNAL_SECRET}"
fi

SECTIONS=(
    section_1_health
    section_2_query_simple
    section_3_query_panorama
    section_4_query_followup
    section_5_query_ambiguous
    section_6_query_error_iteration_cap
    section_7_query_budget_cap
    section_8_clear_history
    section_9_inspect_dispatch
    section_10_inspect_tokens_today
    section_11_telegram_webhook_smoke
)

HTTP_STATUS=""
RESPONSE_BODY=""

step() { printf "\n\033[1;36m-- %s\033[0m\n" "$*"; }
pass() { printf "   \033[32mOK\033[0m %s\n" "$*"; }
fail() {
    printf "   \033[31mFAIL\033[0m %s\n" "$*" >&2
    exit 1
}
todo() { printf "   \033[33mTODO\033[0m %s\n" "$*"; }
note() { printf "   \033[33mnote\033[0m %s\n" "$*"; }

list_sections() {
    printf "%s\n" "${SECTIONS[@]}"
}

check_required_env() {
    command -v curl >/dev/null || fail "curl no esta instalado"
    command -v jq >/dev/null || fail "jq no esta instalado"

    [[ -n "${INTERNAL_SECRET}" ]] || fail "INTERNAL_SECRET no seteado"
    [[ -n "${TEST_USER_ID}" ]] || fail "TEST_USER_ID no seteado"
    [[ "${TEST_USER_ID}" =~ ^[0-9]+$ ]] || fail "TEST_USER_ID debe ser int Telegram from.id"
}

redacted_curl() {
    local method="$1"
    local path="$2"
    local body="${3:-}"
    local auth="${4:-auth}"
    local url="${BASE_URL%/}${path}"

    printf "+ curl -sS -w '\\n%%{http_code}\\n' -X %q" "${method}"
    if [[ "${auth}" == "auth" ]]; then
        printf " -H %q" "${AUTH_HEADER_NAME}: <redacted>"
    fi
    printf " -H %q" "Content-Type: application/json"
    if [[ -n "${body}" ]]; then
        printf " --data %q" "${body}"
    fi
    printf " %q\n" "${url}"
}

request_json() {
    local method="$1"
    local path="$2"
    local body="${3:-}"
    local auth="${4:-auth}"
    local url="${BASE_URL%/}${path}"
    local raw=""
    local curl_args=(-sS -w $'\n%{http_code}\n' -X "${method}")

    redacted_curl "${method}" "${path}" "${body}" "${auth}"

    if [[ "${auth}" == "auth" ]]; then
        curl_args+=(-H "${AUTH_HEADER_NAME}: ${INTERNAL_SECRET}")
    fi
    curl_args+=(-H "Content-Type: application/json")
    if [[ -n "${body}" ]]; then
        curl_args+=(--data "${body}")
    fi

    raw=$(curl "${curl_args[@]}" "${url}")
    printf "%s\n" "${raw}"

    HTTP_STATUS=$(printf "%s\n" "${raw}" | tail -n 1)
    RESPONSE_BODY=$(printf "%s\n" "${raw}" | sed '$d')
}

query_body() {
    local query="$1"
    jq -n --argjson user_id "${TEST_USER_ID}" --arg query "${query}" \
        '{user_id: $user_id, query: $query}'
}

post_query() {
    local query="$1"
    local body
    body=$(query_body "${query}")
    request_json POST "/api/v1/queries/test" "${body}" auth
}

assert_status() {
    local expected="$1"
    [[ "${HTTP_STATUS}" == "${expected}" ]] || {
        printf "%s\n" "${RESPONSE_BODY}" | jq . 2>/dev/null || printf "%s\n" "${RESPONSE_BODY}"
        fail "esperaba HTTP ${expected}, obtuve ${HTTP_STATUS}"
    }
    pass "HTTP ${expected}"
}

assert_jq() {
    local expr="$1"
    local message="$2"
    if jq -e "${expr}" >/dev/null <<<"${RESPONSE_BODY}"; then
        pass "${message}"
    else
        printf "%s\n" "${RESPONSE_BODY}" | jq . 2>/dev/null || printf "%s\n" "${RESPONSE_BODY}"
        fail "${message}"
    fi
}

has_tool() {
    local tool_name="$1"
    jq -e --arg tool "${tool_name}" \
        '.tools_used | any(.name == $tool)' >/dev/null <<<"${RESPONSE_BODY}"
}

require_app_user_for_state() {
    [[ -n "${APP_USER_ID}" ]] || {
        todo "APP_USER_ID no seteado. Para Redis/DB: export APP_USER_ID=<users.id>. En dev con X-User-Id se infiere de INTERNAL_SECRET."
        return 1
    }
    [[ "${APP_USER_ID}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || {
        fail "APP_USER_ID debe ser UUID"
    }
}

psql_available() {
    command -v psql >/dev/null || {
        todo "psql no esta instalado; se omite seccion DB."
        return 1
    }
}

redis_available() {
    if command -v redis-cli >/dev/null; then
        return 0
    fi
    if [[ -n "${REDIS_CONTAINER}" ]] && command -v docker >/dev/null; then
        return 0
    fi
    if command -v docker >/dev/null && docker compose ps -q redis >/dev/null 2>&1; then
        return 0
    fi
    todo "redis-cli no esta instalado y no hay fallback Docker disponible; se omite history."
    return 1
}

psql_exec() {
    local sql="$1"
    local redacted_sql="${sql//${APP_USER_ID}/<app_user_id>}"
    printf "+ psql -v ON_ERROR_STOP=1 -AtX <PSQL_URL redacted> -c %q\n" "${redacted_sql}" >&2
    psql -v ON_ERROR_STOP=1 -AtX "${PSQL_URL}" -c "${sql}"
}

psql_scalar() {
    local sql="$1"
    psql -v ON_ERROR_STOP=1 -AtX "${PSQL_URL}" -c "${sql}"
}

history_key() {
    printf "query_history:%s" "${APP_USER_ID}"
}

redis_cmd() {
    if command -v redis-cli >/dev/null; then
        {
            printf "+ redis-cli -u <REDIS_URL redacted>"
            printf " %q" "$@"
            printf "\n"
        } >&2
        redis-cli -u "${REDIS_URL}" "$@"
        return
    fi

    if [[ -n "${REDIS_CONTAINER}" ]] && command -v docker >/dev/null; then
        {
            printf "+ docker exec -i %q redis-cli" "${REDIS_CONTAINER}"
            printf " %q" "$@"
            printf "\n"
        } >&2
        docker exec -i "${REDIS_CONTAINER}" redis-cli "$@"
        return
    fi

    if command -v docker >/dev/null && docker compose ps -q redis >/dev/null 2>&1; then
        {
            printf "+ docker compose exec -T redis redis-cli"
            printf " %q" "$@"
            printf "\n"
        } >&2
        docker compose exec -T redis redis-cli "$@"
        return
    fi

    return 127
}

clear_history_if_possible() {
    local why="$1"
    local key

    [[ -n "${APP_USER_ID}" ]] || {
        note "history no limpiado (${why}): APP_USER_ID no seteado"
        return 0
    }
    redis_available || {
        note "history no limpiado (${why}): no hay redis-cli/Docker"
        return 0
    }

    key=$(history_key)
    redis_cmd DEL "${key}" >/dev/null
    pass "history limpiado (${why})"
}

section_1_health() {
    step "section_1_health - GET /health responde"
    note "Si falla: servicio apagado, BASE_URL incorrecto o reverse proxy mal apuntado."

    request_json GET "/health" "" none
    assert_status "200"
    assert_jq '.status == "ok"' 'body status == "ok"'
}

section_2_query_simple() {
    step "section_2_query_simple - query simple usa aggregate_transactions"
    note "Si falla 401: auth header/token incorrectos. Si falla 403: TEST_USER_ID no coincide con users.telegram_user_id."
    clear_history_if_possible "section_2 deterministica"

    post_query "cuanto gaste esta semana"
    assert_status "200"
    assert_jq '.reply | type == "string" and length > 0' "reply no vacio"
    assert_jq '.dispatch_id | type == "string" and length > 0' "dispatch_id UUID string presente"
    assert_jq '.iterations >= 1' "iterations >= 1"
    has_tool "aggregate_transactions" || fail "tools_used no contiene aggregate_transactions"
    pass "tools_used contiene aggregate_transactions"
}

section_3_query_panorama() {
    step "section_3_query_panorama - panorama usa varias tools y HTML"
    note "Si tools_used < 3: revisar paralelizacion/tool choice del query dispatcher."
    clear_history_if_possible "section_3 deterministica"

    post_query "dame mi panorama"
    assert_status "200"
    assert_jq '.reply | type == "string" and length > 0' "reply no vacio"
    assert_jq '.tools_used | length >= 3' "tools_used tiene >= 3 tools"
    assert_jq '.reply | test("<b>.*</b>")' 'reply contiene tags <b>...</b>'
}

section_4_query_followup() {
    step "section_4_query_followup - misma conversacion usa history"
    note "Esta seccion es intencionalmente secuencial; las demas no dependen de estado previo."
    clear_history_if_possible "section_4 inicio secuencial"

    post_query "que gaste esta semana"
    assert_status "200"
    assert_jq '.reply | length > 0' "primer reply no vacio"

    post_query "y la semana pasada?"
    assert_status "200"
    assert_jq '.reply | length > 0' "segundo reply no vacio"

    read -r prev_start prev_end < <(
        python3 - <<'PY'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

tz = ZoneInfo(os.environ.get("QUERY_TZ", "America/Costa_Rica"))
today = datetime.now(tz).date()
this_monday = today - timedelta(days=today.weekday())
prev_start = this_monday - timedelta(days=7)
prev_end = this_monday - timedelta(days=1)
print(prev_start.isoformat(), prev_end.isoformat())
PY
    )

    if jq -e --arg start "${prev_start}" --arg end "${prev_end}" '
        .tools_used
        | any(
            .name == "aggregate_transactions"
            and .args_summary.start_date == $start
            and .args_summary.end_date == $end
        )
    ' >/dev/null <<<"${RESPONSE_BODY}"; then
        pass "follow-up resolvio semana anterior (${prev_start}..${prev_end}) via aggregate_transactions"
    else
        printf "%s\n" "${RESPONSE_BODY}" | jq '{tools_used}'
        fail "follow-up no evidencia rango de semana anterior en tools_used"
    fi

    if require_app_user_for_state && redis_available; then
        local key
        key=$(history_key)
        redis_cmd GET "${key}" | jq .
        pass "history inspeccionado en Redis"
    fi
}

section_5_query_ambiguous() {
    step "section_5_query_ambiguous - ambiguedad pregunta antes de tool calls"
    note "Si iterations > 0: el modelo esta ejecutando tools antes de clarificar."
    clear_history_if_possible "section_5 sin contexto previo"

    post_query "que debo pagar"
    assert_status "200"
    assert_jq '.iterations == 0' "iterations == 0"
    assert_jq '.reply | test("pagos recurrentes|deudas|prestamos|préstamos"; "i")' \
        "reply contiene pregunta de clarificacion"
}

section_6_query_error_iteration_cap() {
    step "section_6_query_error_iteration_cap - TODO sin override limpio"
    todo "No hay endpoint admin para bajar LLM_QUERY_ITERATION_CAP ni query deterministica que fuerce loop sin ensuciar codigo."
    note "Verificacion manual esperada: HTTP 200, error_category=iteration_cap, reply del catalogo handle_query_error."
}

cleanup_budget_seed() {
    if [[ "${BUDGET_SEED_ACTIVE:-0}" == "1" ]] && command -v psql >/dev/null && [[ -n "${APP_USER_ID}" ]]; then
        psql -v ON_ERROR_STOP=1 -AtX "${PSQL_URL}" \
            -c "DELETE FROM llm_query_dispatches WHERE user_id = '${APP_USER_ID}'::uuid AND message_hash = 'phase6a-curl-budget-cap';" >/dev/null || true
    fi
}

section_7_query_budget_cap() {
    step "section_7_query_budget_cap - seed DB sobre cap y verificar 429"
    note "Contrato real del endpoint: 429 con detail. El bot path muestra texto al usuario."

    require_app_user_for_state || return 0
    psql_available || return 0

    local before_count
    local after_count
    local expected_count
    local seed_id
    seed_id=$(python3 -c 'import uuid; print(uuid.uuid4())')

    BUDGET_SEED_ACTIVE=1
    trap cleanup_budget_seed RETURN
    trap cleanup_budget_seed EXIT

    psql_exec "DELETE FROM llm_query_dispatches WHERE user_id = '${APP_USER_ID}'::uuid AND message_hash = 'phase6a-curl-budget-cap';" >/dev/null
    before_count=$(psql_scalar "SELECT count(*) FROM llm_query_dispatches WHERE user_id = '${APP_USER_ID}'::uuid;")

    psql_exec "
        INSERT INTO llm_query_dispatches (
            id,
            user_id,
            message_hash,
            total_iterations,
            total_input_tokens,
            total_output_tokens,
            cache_read_input_tokens,
            cache_creation_input_tokens,
            tools_used,
            final_response_chars,
            error,
            duration_ms,
            created_at
        )
        VALUES (
            '${seed_id}'::uuid,
            '${APP_USER_ID}'::uuid,
            'phase6a-curl-budget-cap',
            0,
            120000,
            0,
            0,
            0,
            '[]'::jsonb,
            0,
            'phase6a curl budget seed',
            0,
            now()
        );" >/dev/null
    pass "seed budget row insertado (${seed_id})"

    post_query "cuanto gaste hoy"
    assert_status "429"
    assert_jq '.detail | type == "string" and length > 0' "budget cap devuelve detail"

    after_count=$(psql_scalar "SELECT count(*) FROM llm_query_dispatches WHERE user_id = '${APP_USER_ID}'::uuid;")
    expected_count=$((before_count + 1))
    [[ "${after_count}" == "${expected_count}" ]] || fail "budget cap inserto rows nuevas: before=${before_count}, after=${after_count}, esperado=${expected_count}"
    pass "no se inserto nuevo llm_query_dispatch fuera del seed"

    cleanup_budget_seed
    BUDGET_SEED_ACTIVE=0
    trap - RETURN
    trap - EXIT
    pass "cleanup seed budget ejecutado"
}

section_8_clear_history() {
    step "section_8_clear_history - limpiar query_history"
    todo "No hay endpoint HTTP equivalente a /clear. Se usa redis-cli directo si APP_USER_ID esta disponible."

    require_app_user_for_state || return 0
    redis_available || return 0

    local key
    key=$(history_key)
    redis_cmd DEL "${key}" >/dev/null
    [[ -z "$(redis_cmd GET "${key}" | tail -n +2)" ]] || fail "history no quedo vacio"
    pass "query_history limpio"
}

section_9_inspect_dispatch() {
    step "section_9_inspect_dispatch - inspeccionar ultimo llm_query_dispatch"
    note "Verificar visualmente tools_used, tokens y duration_ms."

    require_app_user_for_state || return 0
    psql_available || return 0

    psql_exec "
        SELECT jsonb_pretty(
            jsonb_build_object(
                'id', id,
                'created_at', created_at,
                'iterations', total_iterations,
                'tools_used', tools_used,
                'total_input_tokens', total_input_tokens,
                'total_output_tokens', total_output_tokens,
                'cache_read_input_tokens', COALESCE(cache_read_input_tokens, 0),
                'duration_ms', duration_ms,
                'error', error
            )
        )
        FROM llm_query_dispatches
        WHERE user_id = '${APP_USER_ID}'::uuid
        ORDER BY created_at DESC
        LIMIT 1;"
}

section_10_inspect_tokens_today() {
    step "section_10_inspect_tokens_today - token spend del dia"
    note "Mismo calculo conceptual que api.services.budget: input+output, sin cache_read."

    require_app_user_for_state || return 0
    psql_available || return 0

    psql_exec "
        WITH bounds AS (
            SELECT (
                date_trunc('day', now() AT TIME ZONE '${QUERY_TZ}')
                AT TIME ZONE '${QUERY_TZ}'
            ) AS cutoff_utc
        ),
        extraction AS (
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM llm_extractions, bounds
            WHERE user_id = '${APP_USER_ID}'::uuid
              AND created_at >= bounds.cutoff_utc
        ),
        query_dispatch AS (
            SELECT
                COALESCE(SUM(total_input_tokens), 0) AS input_tokens,
                COALESCE(SUM(total_output_tokens), 0) AS output_tokens
            FROM llm_query_dispatches, bounds
            WHERE user_id = '${APP_USER_ID}'::uuid
              AND created_at >= bounds.cutoff_utc
        )
        SELECT jsonb_pretty(
            jsonb_build_object(
                'timezone', '${QUERY_TZ}',
                'extraction_tokens', extraction.input_tokens + extraction.output_tokens,
                'query_dispatch_tokens', query_dispatch.input_tokens + query_dispatch.output_tokens,
                'total_tokens_today',
                    extraction.input_tokens + extraction.output_tokens
                    + query_dispatch.input_tokens + query_dispatch.output_tokens
            )
        )
        FROM extraction, query_dispatch;"
}

section_11_telegram_webhook_smoke() {
    step "section_11_telegram_webhook_smoke - update Telegram simulado"

    if [[ "${TELEGRAM_MODE}" != "webhook" ]]; then
        todo "TELEGRAM_MODE=${TELEGRAM_MODE}; bot en polling/disabled. Webhook smoke omitido."
        return 0
    fi
    [[ -n "${TELEGRAM_WEBHOOK_SECRET}" ]] || {
        todo "TELEGRAM_WEBHOOK_SECRET no seteado; no se puede simular webhook."
        return 0
    }

    local body
    body=$(jq -n --argjson tg_id "${TEST_USER_ID}" '{
        update_id: 600000001,
        message: {
            message_id: 1,
            date: 1777411200,
            chat: {id: $tg_id, type: "private"},
            from: {id: $tg_id, is_bot: false, first_name: "Phase6a"},
            text: "hola"
        }
    }')

    redacted_curl POST "/api/v1/telegram/webhook" "${body}" none
    local raw
    raw=$(curl -sS -w $'\n%{http_code}\n' -X POST \
        -H "Content-Type: application/json" \
        -H "X-Telegram-Bot-Api-Secret-Token: ${TELEGRAM_WEBHOOK_SECRET}" \
        --data "${body}" \
        "${BASE_URL%/}/api/v1/telegram/webhook")
    printf "%s\n" "${raw}"
    HTTP_STATUS=$(printf "%s\n" "${raw}" | tail -n 1)
    RESPONSE_BODY=$(printf "%s\n" "${raw}" | sed '$d')
    assert_status "200"
    assert_jq '.ok == true' "webhook respondio ok=true"
}

resolve_section() {
    local requested="$1"
    local matches=()
    local section

    for section in "${SECTIONS[@]}"; do
        if [[ "${section}" == "${requested}" || "${section}" == "${requested}"_* ]]; then
            matches+=("${section}")
        fi
    done

    case "${#matches[@]}" in
        0)
            fail "seccion desconocida: ${requested}. Usa --list."
            ;;
        1)
            printf "%s\n" "${matches[0]}"
            ;;
        *)
            fail "alias ambiguo: ${requested} -> ${matches[*]}"
            ;;
    esac
}

main() {
    if [[ "${1:-}" == "--list" ]]; then
        list_sections
        return 0
    fi

    check_required_env

    if [[ $# -eq 0 ]]; then
        local section
        for section in "${SECTIONS[@]}"; do
            "${section}"
        done
        return 0
    fi

    local resolved
    resolved=$(resolve_section "$1")
    "${resolved}"
}

main "$@"
