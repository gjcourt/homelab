package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sync"
	"time"
)

// sseClient represents a single SSE subscriber.
type sseClient struct {
	id         string
	account    string
	done       chan struct{}
	buffer     chan string
	messages   chan map[string]interface{}
	heartbeats chan time.Time
}

// sseHub manages multiple SSE client connections.
type sseHub struct {
	clients   map[string]*sseClient
	mu        sync.RWMutex
	metrics   *Metrics
	config    Config
	signalRPC *SignalRPC
}

func newSSEHub(cfg Config, metrics *Metrics, rpc *SignalRPC) *sseHub {
	h := &sseHub{
		clients:   make(map[string]*sseClient),
		metrics:   metrics,
		config:    cfg,
		signalRPC: rpc,
	}
	go h.pollLoop()
	go h.heartbeatLoop()
	return h
}

func (h *sseHub) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !h.config.CheckBearer(r.Header.Get("Authorization")) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	account := r.URL.Query().Get("account")
	if account == "" {
		http.Error(w, "account parameter required", http.StatusBadRequest)
		return
	}

	if !h.config.IsAccountAllowed(account) {
		http.Error(w, "account not allowed", http.StatusForbidden)
		return
	}

	accept := r.Header.Get("Accept")
	if accept == "" || containsSSE(accept) {
		h.handleSSE(w, r, account)
	} else {
		h.handleREST(w, r, account)
	}
}

func (h *sseHub) handleSSE(w http.ResponseWriter, r *http.Request, account string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "SSE streaming not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("X-Accel-Buffering", "no")

	clientID := fmt.Sprintf("%s-%d", account, time.Now().UnixNano())
	client := &sseClient{
		id:         clientID,
		account:    account,
		done:       make(chan struct{}),
		buffer:     make(chan string, 100),
		messages:   make(chan map[string]interface{}, 100),
		heartbeats: make(chan time.Time, 10),
	}

	h.mu.Lock()
	h.clients[clientID] = client
	h.mu.Unlock()

	h.metrics.SSEConnections.Inc()
	h.metrics.SSEConnectionsTotal.Inc()

	log.Printf("SSE: client %s connected (account=%s, total=%d)", clientID, account, len(h.clients))
	defer func() {
		close(client.done)
		h.mu.Lock()
		delete(h.clients, clientID)
		h.mu.Unlock()
		h.metrics.SSEConnections.Dec()
		log.Printf("SSE: client %s disconnected (account=%s, total=%d)", clientID, account, len(h.clients))
	}()

	ctx := r.Context()

	fmt.Fprintf(w, "retry: 3000\ndata: {\"type\":\"connected\",\"clientId\":\"%s\",\"timestamp\":%d}\n\n", clientID, time.Now().UnixMilli())
	flusher.Flush()

	// Drain any pre-buffered events.
	for {
		select {
		case msg := <-client.buffer:
			w.Write([]byte(msg))
			flusher.Flush()
		default:
			goto sendLoop
		}
	}

sendLoop:
	for {
		select {
		case <-ctx.Done():
			return
		case msg := <-client.messages:
			data, _ := json.Marshal(msg)
			fmt.Fprintf(w, "event: message\ndata: %s\n\n", data)
			h.metrics.RecordMessage()
			flusher.Flush()
		case <-client.done:
			return
		}
	}
}

func (h *sseHub) handleREST(w http.ResponseWriter, r *http.Request, account string) {
	messages, err := h.signalRPC.receive(account)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(messages)
}

// pollLoop polls each allowed account separately so messages are never cross-delivered.
func (h *sseHub) pollLoop() {
	ticker := time.NewTicker(h.config.PollInterval)
	defer ticker.Stop()

	for range ticker.C {
		for _, account := range h.config.AllowedAccounts {
			h.pollAccount(account)
		}
	}
}

func (h *sseHub) pollAccount(account string) {
	messages, err := h.signalRPC.receive(account)
	if err != nil {
		log.Printf("poll error for %s: %v", account, err)
		return
	}

	if len(messages) == 0 {
		return
	}

	data, _ := json.Marshal(messages)
	event := fmt.Sprintf("event: message\ndata: %s\n\n", data)

	h.mu.RLock()
	for _, client := range h.clients {
		if client.account != account {
			continue
		}
		select {
		case client.buffer <- event:
		default:
			log.Printf("poll: buffer full for %s, dropping event", account)
		}
	}
	for _, msg := range messages {
		for _, client := range h.clients {
			if client.account != account {
				continue
			}
			select {
			case client.messages <- msg:
			default:
			}
		}
	}
	h.mu.RUnlock()
}

func (h *sseHub) heartbeatLoop() {
	ticker := time.NewTicker(h.config.HeartbeatInterval)
	defer ticker.Stop()

	for range ticker.C {
		event := "event: heartbeat\ndata: {\"type\":\"heartbeat\",\"timestamp\":" +
			fmt.Sprintf("%d", time.Now().UnixMilli()) + "}\n\n"

		h.mu.RLock()
		for _, client := range h.clients {
			select {
			case client.buffer <- event:
			default:
			}
		}
		h.mu.RUnlock()
	}
}

func containsSSE(accept string) bool {
	return len(accept) >= len("text/event-stream") &&
		accept[:len("text/event-stream")] == "text/event-stream"
}
