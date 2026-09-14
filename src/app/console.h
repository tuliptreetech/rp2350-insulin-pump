/*
 * console.h - Line-oriented control and telemetry over the stdio UART.
 *
 * A shipping pump would put this behind buttons and a radio. Here it is the
 * service port: it is how a test harness drives the pump and how it reads
 * back a machine-parseable view of delivery, so scenarios can be checked
 * without a debugger attached.
 */
#ifndef CONSOLE_H
#define CONSOLE_H

#include "app/safety.h"

void console_init(void);

/* Drain any typed input and act on complete lines. Non-blocking. */
void console_poll(void);

/* Emit one telemetry record. Called on a timer by main(). */
void console_telemetry(const safety_inputs_t *in);

#endif /* CONSOLE_H */
