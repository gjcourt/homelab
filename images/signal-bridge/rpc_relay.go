package main

import (
	"encoding/json"
	"net/http"
)

type jsonRPCRequest struct {
	JSONRPC string                 `json:"jsonrpc"`
	Method  string                 `json:"method"`
	Params  map[string]interface{} `json:"params,omitempty"`
	ID      *int                   `json:"id,omitempty"`
}

type jsonRPCResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	Result  interface{} `json:"result,omitempty"`
	Error   interface{} `json:"error,omitempty"`
	ID      *int        `json:"id,omitempty"`
}

func relayRPC(w http.ResponseWriter, r *http.Request, rpc *SignalRPC, cfg Config) {
	if !cfg.CheckBearer(r.Header.Get("Authorization")) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	var req jsonRPCRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(jsonRPCResponse{
			JSONRPC: "2.0",
			Error:   map[string]interface{}{"code": -32700, "message": "parse error"},
		})
		return
	}

	// Validate account against allowlist.
	if len(cfg.AllowedAccounts) > 0 {
		account, _ := req.Params["account"].(string)
		if !cfg.IsAccountAllowed(account) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusForbidden)
			json.NewEncoder(w).Encode(jsonRPCResponse{
				JSONRPC: "2.0",
				Error:   map[string]interface{}{"code": -32603, "message": "account not allowed"},
				ID:      req.ID,
			})
			return
		}
	}

	// Map Hermes-style method names to signal-cli JSON-RPC methods.
	method := req.Method
	switch method {
	case "signal.send":
		method = "send"
	case "signal.getInfo":
		method = "getInfo"
	case "signal.receive":
		method = "receive"
	}

	result, err := rpc.call(method, req.Params)
	w.Header().Set("Content-Type", "application/json")

	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(jsonRPCResponse{
			JSONRPC: "2.0",
			Error:   map[string]interface{}{"code": -32603, "message": err.Error()},
			ID:      req.ID,
		})
		return
	}

	json.NewEncoder(w).Encode(jsonRPCResponse{
		JSONRPC: "2.0",
		Result:  result,
		ID:      req.ID,
	})
}
