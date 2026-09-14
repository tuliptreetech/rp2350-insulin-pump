/*
 * ui.h - What the patient sees on the 128x64 OLED.
 *
 * One screen, no menus: the pump's job is to make its state readable at a
 * glance. The headline number is whatever is actually happening right now -
 * the basal rate normally, bolus progress during a bolus, and the alarm text
 * when something is wrong.
 */
#ifndef UI_H
#define UI_H

#include "app/safety.h"

bool ui_init(void);

/* Redraw the screen and push it to the panel. Call a couple of times a second. */
void ui_render(const safety_inputs_t *in);

#endif /* UI_H */
