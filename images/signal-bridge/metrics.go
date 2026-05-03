package main

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

type Metrics struct {
	PollsTotal              *prometheus.CounterVec
	MessagesTotal           prometheus.Counter
	SSEConnections          prometheus.Gauge
	SSEConnectionsTotal     prometheus.Counter
	PollDuration            prometheus.Histogram
	PollMessagesTotal       prometheus.Histogram
	LastPollAge             prometheus.Gauge
	LastMessageAge          prometheus.Gauge
	RPCRequestsTotal        *prometheus.CounterVec
	RPCDuration             prometheus.Histogram
	TCPErrorsTotal          *prometheus.CounterVec
}

func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		PollsTotal: promauto.With(reg).NewCounterVec(
			prometheus.CounterOpts{
				Name: "signal_bridge_polls_total",
				Help: "Total number of polls sent to signal-cli",
			},
			[]string{"status"},
		),
		MessagesTotal: promauto.With(reg).NewCounter(
			prometheus.CounterOpts{
				Name: "signal_bridge_messages_total",
				Help: "Total SSE message events sent to all clients",
			},
		),
		SSEConnections: promauto.With(reg).NewGauge(
			prometheus.GaugeOpts{
				Name: "signal_bridge_sse_connections",
				Help: "Current number of active SSE client connections",
			},
		),
		SSEConnectionsTotal: promauto.With(reg).NewCounter(
			prometheus.CounterOpts{
				Name: "signal_bridge_sse_connections_total",
				Help: "Total SSE connection openings (reconnects included)",
			},
		),
		PollDuration: promauto.With(reg).NewHistogram(
			prometheus.HistogramOpts{
				Name:    "signal_bridge_poll_duration_seconds",
				Help:    "Time to complete one poll roundtrip to signal-cli",
				Buckets: []float64{0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0},
			},
		),
		PollMessagesTotal: promauto.With(reg).NewHistogram(
			prometheus.HistogramOpts{
				Name:    "signal_bridge_poll_messages_total",
				Help:    "Number of messages returned per poll",
				Buckets: []float64{0, 1, 2, 5, 10, 20, 50},
			},
		),
		LastPollAge: promauto.With(reg).NewGauge(
			prometheus.GaugeOpts{
				Name: "signal_bridge_last_poll_age_seconds",
				Help: "Seconds since the last successful poll",
			},
		),
		LastMessageAge: promauto.With(reg).NewGauge(
			prometheus.GaugeOpts{
				Name: "signal_bridge_last_message_age_seconds",
				Help: "Seconds since the last message event was emitted to SSE clients",
			},
		),
		RPCRequestsTotal: promauto.With(reg).NewCounterVec(
			prometheus.CounterOpts{
				Name: "signal_bridge_rpc_requests_total",
				Help: "Total JSON-RPC requests relayed to signal-cli",
			},
			[]string{"method"},
		),
		RPCDuration: promauto.With(reg).NewHistogram(
			prometheus.HistogramOpts{
				Name:    "signal_bridge_rpc_duration_seconds",
				Help:    "Time to complete each JSON-RPC request",
				Buckets: []float64{0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0},
			},
		),
		TCPErrorsTotal: promauto.With(reg).NewCounterVec(
			prometheus.CounterOpts{
				Name: "signal_bridge_tcp_errors_total",
				Help: "TCP connection errors to signal-cli",
			},
			[]string{"error_type"},
		),
	}
	return m
}

func (m *Metrics) RecordPoll(ok bool, duration time.Duration, msgCount int) {
	if ok {
		m.PollsTotal.WithLabelValues("ok").Inc()
	} else {
		m.PollsTotal.WithLabelValues("error").Inc()
	}
	m.PollDuration.Observe(duration.Seconds())
	m.PollMessagesTotal.Observe(float64(msgCount))
	m.LastPollAge.Set(0)
}

func (m *Metrics) RecordMessage() {
	m.MessagesTotal.Inc()
	m.LastMessageAge.Set(0)
}

func (m *Metrics) RecordRPC(method string, duration time.Duration) {
	m.RPCRequestsTotal.WithLabelValues(method).Inc()
	m.RPCDuration.Observe(duration.Seconds())
}

func (m *Metrics) RecordTCPError(errorType string) {
	m.TCPErrorsTotal.WithLabelValues(errorType).Inc()
}
