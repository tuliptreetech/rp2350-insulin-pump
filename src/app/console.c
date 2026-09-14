#include "app/console.h"

#include <stdio.h>
#include <string.h>

#include "app/pump.h"
#include "board.h"
#include "drivers/drv8825.h"
#include "pico/stdlib.h"

#define LINE_MAX 48

static char s_line[LINE_MAX];
static size_t s_len;

/*
 * Parse a decimal dose like "2.5" or ".025" into milliunits. Returns false on
 * anything it does not fully understand - a dose is not a place to guess at
 * what the user meant.
 */
static bool parse_milliunits(const char *s, uint32_t *out)
{
    uint32_t whole = 0, frac = 0, scale = 1000;
    bool any_digit = false;

    while (*s == ' ') s++;

    while (*s >= '0' && *s <= '9') {
        if (whole > 100000) return false;
        whole = whole * 10 + (uint32_t)(*s++ - '0');
        any_digit = true;
    }
    if (*s == '.') {
        s++;
        while (*s >= '0' && *s <= '9') {
            if (scale > 1) {
                scale /= 10;
                frac += (uint32_t)(*s - '0') * scale;
            }
            s++;
            any_digit = true;
        }
    }
    while (*s == ' ') s++;
    if (!any_digit || *s != '\0') {
        return false;
    }

    *out = whole * 1000 + frac;
    return true;
}

static void print_help(void)
{
    printf("commands:\n"
           "  run              start basal delivery\n"
           "  stop             stop delivery (user)\n"
           "  basal <U/hr>     set basal rate, max %u.%03u U/hr\n"
           "  bolus <U>        deliver a bolus, max %u U\n"
           "  cancel           abort the bolus in progress\n"
           "  ack              acknowledge alarms and resume if clear\n"
           "  reservoir        fit a fresh cartridge\n"
           "  status           print pump state\n",
           (unsigned)(PUMP_MAX_BASAL_MU_PER_HR / 1000),
           (unsigned)(PUMP_MAX_BASAL_MU_PER_HR % 1000),
           (unsigned)(PUMP_MAX_BOLUS_MU / 1000));
}

static void print_status(void)
{
    pump_status_t ps;
    pump_get_status(&ps);

    static const char *mode[] = { "stopped", "running", "suspended" };
    printf("status mode=%s basal=%u.%03u_U/hr reservoir=%u.%03u_U "
           "total=%llu.%03llu_U hour=%u.%03u_U alarms=0x%03lx\n",
           mode[ps.mode],
           (unsigned)(ps.basal_mu_per_hr / 1000), (unsigned)(ps.basal_mu_per_hr % 1000),
           (unsigned)(ps.reservoir_mu / 1000), (unsigned)(ps.reservoir_mu % 1000),
           (unsigned long long)(ps.delivered_total_mu / 1000),
           (unsigned long long)(ps.delivered_total_mu % 1000),
           (unsigned)(ps.delivered_hour_mu / 1000), (unsigned)(ps.delivered_hour_mu % 1000),
           (unsigned long)safety_active());
}

static void handle_line(char *line)
{
    while (*line == ' ') line++;
    if (*line == '\0') {
        return;
    }

    char *arg = strchr(line, ' ');
    if (arg) {
        *arg++ = '\0';
    }

    if (strcmp(line, "run") == 0) {
        if (safety_delivery_blocked()) {
            printf("error alarms active, 'ack' first\n");
            return;
        }
        pump_start();
        printf("ok running\n");
    } else if (strcmp(line, "stop") == 0) {
        pump_stop();
        printf("ok stopped\n");
    } else if (strcmp(line, "basal") == 0) {
        uint32_t mu;
        if (!arg || !parse_milliunits(arg, &mu)) {
            printf("error bad rate\n");
        } else if (!pump_set_basal(mu)) {
            printf("error rate above limit\n");
        } else {
            printf("ok basal %u.%03u U/hr\n", (unsigned)(mu / 1000), (unsigned)(mu % 1000));
        }
    } else if (strcmp(line, "bolus") == 0) {
        uint32_t mu;
        if (!arg || !parse_milliunits(arg, &mu)) {
            printf("error bad dose\n");
            return;
        }
        pump_dose_result_t r = pump_start_bolus(mu);
        if (r == PUMP_DOSE_OK) {
            printf("ok bolus %u.%03u U\n", (unsigned)(mu / 1000), (unsigned)(mu % 1000));
        } else {
            printf("error %s\n", pump_dose_result_str(r));
        }
    } else if (strcmp(line, "cancel") == 0) {
        pump_cancel_bolus();
        printf("ok bolus cancelled\n");
    } else if (strcmp(line, "ack") == 0) {
        uint32_t left = safety_acknowledge();
        printf("ok alarms=0x%03lx\n", (unsigned long)left);
    } else if (strcmp(line, "reservoir") == 0) {
        pump_replace_reservoir();
        printf("ok reservoir full\n");
    } else if (strcmp(line, "status") == 0) {
        print_status();
    } else if (strcmp(line, "help") == 0 || strcmp(line, "?") == 0) {
        print_help();
    } else {
        printf("error unknown command '%s'\n", line);
    }
}

void console_init(void)
{
    s_len = 0;
    printf("\nRP2350 insulin pump ready. 'help' for commands.\n");
}

void console_poll(void)
{
    int c;
    while ((c = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
        if (c == '\r' || c == '\n') {
            s_line[s_len] = '\0';
            handle_line(s_line);
            s_len = 0;
        } else if (s_len + 1 < sizeof s_line) {
            s_line[s_len++] = (char)c;
        }
        /* Overlong lines are truncated rather than split into two commands. */
    }
}

void console_telemetry(const safety_inputs_t *in)
{
    pump_status_t ps;
    pump_get_status(&ps);

    printf("t=%llu mode=%d basal=%u bolus=%u/%u resv=%u total=%llu hour=%u "
           "steps=%lld flow_nlpm=%ld flow_st=%d air=%d psi_m=%ld psi_raw=%u "
           "nfault=%d alarms=0x%03lx\n",
           (unsigned long long)(time_us_64() / 1000),
           (int)ps.mode, (unsigned)ps.basal_mu_per_hr,
           (unsigned)ps.bolus_delivered_mu, (unsigned)ps.bolus_total_mu,
           (unsigned)ps.reservoir_mu,
           (unsigned long long)ps.delivered_total_mu, (unsigned)ps.delivered_hour_mu,
           (long long)drv8825_net_microsteps(),
           (long)in->flow.nl_per_min, (int)in->flow_status, (int)in->flow.air_in_line,
           (long)in->pressure.millipsi, in->pressure.raw_counts,
           (int)in->motor_fault,
           (unsigned long)safety_active());
}
