#include "app/ui.h"

#include <stdio.h>
#include <string.h>

#include "app/pump.h"
#include "drivers/ssd1306.h"

/* Render a milli-valued integer as "-12.345", into a caller-owned buffer. */
static const char *fmt_milli(char *buf, size_t n, int64_t milli, int decimals)
{
    const char *sign = "";
    if (milli < 0) {
        sign = "-";
        milli = -milli;
    }
    int64_t whole = milli / 1000;
    int64_t frac = milli % 1000;

    switch (decimals) {
    case 0: snprintf(buf, n, "%s%lld", sign, (long long)whole); break;
    case 1: snprintf(buf, n, "%s%lld.%01lld", sign, (long long)whole, (long long)(frac / 100)); break;
    case 2: snprintf(buf, n, "%s%lld.%02lld", sign, (long long)whole, (long long)(frac / 10)); break;
    default: snprintf(buf, n, "%s%lld.%03lld", sign, (long long)whole, (long long)frac); break;
    }
    return buf;
}

static const char *mode_text(const pump_status_t *ps)
{
    switch (ps->mode) {
    case PUMP_STOPPED:   return "STOPPED";
    case PUMP_RUNNING:   return pump_bolus_active() ? "BOLUS" : "RUNNING";
    case PUMP_SUSPENDED: return "SUSPENDED";
    }
    return "?";
}

bool ui_init(void)
{
    if (!ssd1306_init()) {
        return false;
    }
    ssd1306_clear();
    ssd1306_text(10, 20, "INSULIN", 2);
    ssd1306_text(28, 40, "PUMP", 2);
    return ssd1306_flush();
}

static void draw_status_bar(const pump_status_t *ps)
{
    char buf[40], num[24];

    snprintf(buf, sizeof buf, "%-9s", mode_text(ps));
    ssd1306_text_at(0, 0, buf);

    /* Reservoir, right-aligned in the bar. */
    fmt_milli(num, sizeof num, ps->reservoir_mu, 0);
    snprintf(buf, sizeof buf, "%sU", num);
    int w = (int)strlen(buf) * SSD1306_CHAR_W;
    ssd1306_text(SSD1306_WIDTH - w, 0, buf, 1);

    ssd1306_invert_rect(0, 0, SSD1306_WIDTH, 8);
}

static void draw_headline(const pump_status_t *ps, pump_alarm_t top)
{
    char buf[64], num[24];

    if (top != ALARM__COUNT && top != ALARM_RESERVOIR_LOW) {
        /*
         * An alarm owns the headline; nothing else on screen matters as much.
         *
         * Draw onto the cleared framebuffer and invert afterwards - do not
         * pre-fill the band. Filling it lit first makes the glyphs a no-op
         * (lit pixels drawn onto lit pixels), and the inversion then blanks
         * the whole band, leaving an empty white bar where the alarm text
         * should be.
         */
        const char *name = safety_alarm_name(top);
        int w = (int)strlen(name) * SSD1306_CHAR_W;
        ssd1306_text((SSD1306_WIDTH - w) / 2, 13, name, 1);
        ssd1306_invert_rect(0, 11, SSD1306_WIDTH, 12);
        return;
    }

    if (pump_bolus_active()) {
        fmt_milli(num, sizeof num, ps->bolus_delivered_mu, 2);
        char tot[24];
        fmt_milli(tot, sizeof tot, ps->bolus_total_mu, 2);
        snprintf(buf, sizeof buf, "%s/%sU", num, tot);
        ssd1306_text(0, 11, buf, 2);
        return;
    }

    fmt_milli(num, sizeof num, ps->basal_mu_per_hr, 2);
    snprintf(buf, sizeof buf, "%sU/h", num);
    ssd1306_text(0, 11, buf, 2);
}

/*
 * The progress bar lives entirely inside text row 3 (y24..31) so that row 4
 * stays free for the flow reading. The headline above it is scale-2 text at
 * y11 and the font's eighth row is blank, so it ends at y24 and the bar can
 * start at y27 with a two-pixel gap.
 */
static void draw_bolus_bar(const pump_status_t *ps)
{
    ssd1306_fill_rect(0, 27, SSD1306_WIDTH, 1, true);
    ssd1306_fill_rect(0, 31, SSD1306_WIDTH, 1, true);

    uint32_t pct = ps->bolus_total_mu
        ? (ps->bolus_delivered_mu * 100u) / ps->bolus_total_mu : 0;
    if (pct > 100) pct = 100;
    ssd1306_fill_rect(0, 28, (int)((SSD1306_WIDTH * pct) / 100u), 3, true);
}

void ui_render(const safety_inputs_t *in)
{
    pump_status_t ps;
    pump_get_status(&ps);
    pump_alarm_t top = safety_top_alarm();

    char line[40], num[24];

    ssd1306_clear();
    draw_status_bar(&ps);
    draw_headline(&ps, top);

    if (pump_bolus_active()) {
        draw_bolus_bar(&ps);
    }

    /*
     * Flow, in uL/min, from the sensor that is watching the tubing. Drawn
     * during a bolus as well as outside one: a bolus is the only time this
     * reading means anything, because one count on the SLF3S-1300F is about
     * 2 uL/min and a basal rate is two orders of magnitude below that. The
     * delivery check in safety.c arms on the same reasoning.
     */
    if (in->flow_status == SLF3X_OK) {
        fmt_milli(num, sizeof num, in->flow.nl_per_min, 1);
        snprintf(line, sizeof line, "Flow  %s uL/m", num);
    } else {
        snprintf(line, sizeof line, "Flow  --");
    }
    ssd1306_text_at(0, 4, line);

    if (in->pressure.valid) {
        fmt_milli(num, sizeof num, in->pressure.millipsi, 2);
        snprintf(line, sizeof line, "Line  %s psi", num);
    } else {
        snprintf(line, sizeof line, "Line  -- sensor");
    }
    ssd1306_text_at(0, 5, line);

    fmt_milli(num, sizeof num, (int64_t)ps.delivered_total_mu, 2);
    snprintf(line, sizeof line, "Total %s U", num);
    ssd1306_text_at(0, 6, line);

    if (top == ALARM_RESERVOIR_LOW) {
        snprintf(line, sizeof line, " %s ", safety_alarm_name(top));
        ssd1306_text_at(0, 7, line);
        ssd1306_invert_rect(0, 56, SSD1306_WIDTH, 8);
    } else {
        fmt_milli(num, sizeof num, ps.delivered_hour_mu, 2);
        snprintf(line, sizeof line, "1hr   %s U", num);
        ssd1306_text_at(0, 7, line);
    }

    ssd1306_flush();
}
