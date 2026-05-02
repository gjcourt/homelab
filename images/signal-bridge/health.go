package main

import (
	"encoding/json"
	"log"
	"net/http"
	"time"
)

type healthHandler struct {
	rpc     *SignalRPC
	metrics *Metrics
}

type healthResponse struct {
	Status  string            `json:"status"`
	Signal  string            `json:"signal_cli"`
	Uptime  float64           `json:"uptime_seconds"`
	Details map[string]string `json:"details,omitempty"`
}

var startTime = time.Now()

func (h *healthHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	resp := healthResponse{
		Status:  "ok",
		Signal:  "unknown",
		Uptime:  time.Since(startTime).Seconds(),
		Details: make(map[string]string),
	}

	// Check signal-cli connectivity
	_, err := h.rpc.getInfo()
	if err != nil {
		resp.Status = "degraded"
		resp.Signal = "error"
		resp.Details["signal_error"] = err.Error()
		log.Printf("health: signal-cli check failed: %v", err)
	} else {
		resp.Signal = "ok"
	}

	w.Header().Set("Content-Type", "application/json")
	if resp.Status != "ok" {
		w.WriteHeader(http.StatusServiceUnavailable)
	}
	json.NewEncoder(w).Encode(resp)
}
