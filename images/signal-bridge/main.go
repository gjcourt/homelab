package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
	cfg := LoadConfig()
	log.Printf("signal-bridge starting: signal-cli=%s poll=%s heartbeat=%s listen=%s:%d accounts=%v",
		cfg.SignalCLIAddr(), cfg.PollInterval, cfg.HeartbeatInterval, cfg.ListenAddr, cfg.ListenPort, cfg.AllowedAccounts)

	reg := prometheus.NewRegistry()
	metrics := NewMetrics(reg)

	rpc := NewSignalRPC(cfg, metrics)
	if err := rpc.Connect(); err != nil {
		log.Fatalf("failed to connect to signal-cli: %v", err)
	}

	hub := newSSEHub(cfg, metrics, rpc)
	healthCheck := &healthHandler{rpc: rpc, metrics: metrics}

	mux := http.NewServeMux()

	mux.Handle("/api/v1/events", hub)

	mux.HandleFunc("/api/v1/rpc", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		relayRPC(w, r, rpc, cfg)
	})

	// /api/v1/check is the Hermes health endpoint; /v1/health is kept for compatibility.
	mux.HandleFunc("/api/v1/check", healthCheck.ServeHTTP)
	mux.HandleFunc("/v1/health", healthCheck.ServeHTTP)

	mux.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{
		Registry: reg,
	}))

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `{"status":"ok","service":"signal-bridge"}`)
	})

	server := &http.Server{
		Addr:         fmt.Sprintf("%s:%d", cfg.ListenAddr, cfg.ListenPort),
		Handler:      mux,
		ReadTimeout:  60 * time.Second,
		WriteTimeout: 0, // SSE connections have no write timeout
		IdleTimeout:  120 * time.Second,
	}

	go func() {
		sigChan := make(chan os.Signal, 1)
		signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
		<-sigChan
		log.Println("shutting down...")
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			log.Fatalf("server shutdown error: %v", err)
		}
	}()

	log.Printf("listening on %s:%d", cfg.ListenAddr, cfg.ListenPort)
	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("server error: %v", err)
	}
	log.Println("server stopped")
}
