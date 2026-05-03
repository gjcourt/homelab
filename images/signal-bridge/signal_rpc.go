package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"sync"
	"time"
)

// JSON-RPC 2.0 types
type rpcRequest struct {
	JSONRPC string                 `json:"jsonrpc"`
	Method  string                 `json:"method"`
	Params  map[string]interface{} `json:"params,omitempty"`
	ID      *int                   `json:"id,omitempty"`
}

type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
	ID      *int            `json:"id,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// SignalRPC manages a persistent TCP connection to signal-cli's JSON-RPC interface.
type SignalRPC struct {
	mu      sync.Mutex
	conn    net.Conn
	reader  *bufio.Reader
	nextID  int
	metrics *Metrics
	config  Config
	retry   time.Duration
	lastErr error
}

func NewSignalRPC(cfg Config, metrics *Metrics) *SignalRPC {
	return &SignalRPC{
		config:  cfg,
		metrics: metrics,
		retry:   1 * time.Second,
		nextID:  1,
	}
}

func (s *SignalRPC) Connect() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.conn != nil {
		s.conn.Close()
	}

	conn, err := net.DialTimeout("tcp", s.config.SignalCLIAddr(), 5*time.Second)
	if err != nil {
		s.metrics.RecordTCPError("dial")
		s.lastErr = err
		return fmt.Errorf("dial signal-cli: %w", err)
	}

	s.conn = conn
	s.reader = bufio.NewReader(conn)
	return nil
}

func (s *SignalRPC) call(method string, params map[string]interface{}) (json.RawMessage, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.conn == nil {
		if err := s.reconnectLocked(); err != nil {
			return nil, err
		}
	}

	id := s.nextID
	s.nextID++

	req := rpcRequest{
		JSONRPC: "2.0",
		Method:  method,
		Params:  params,
		ID:      &id,
	}

	data, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	if _, err := s.conn.Write(append(data, '\n')); err != nil {
		s.metrics.RecordTCPError("write")
		s.conn.Close()
		s.conn = nil
		return nil, fmt.Errorf("write request: %w", err)
	}

	resp, err := s.readResponse()
	if err != nil {
		s.conn.Close()
		s.conn = nil
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.Error != nil {
		return nil, fmt.Errorf("rpc error %d: %s", resp.Error.Code, resp.Error.Message)
	}

	return resp.Result, nil
}

// receive fetches pending messages for the given account. Pass empty string to
// use signal-cli's default account (single-account deployments only).
func (s *SignalRPC) receive(account string) ([]map[string]interface{}, error) {
	start := time.Now()

	params := map[string]interface{}{
		"maxMessages": 50,
		"timeout":     1,
	}
	if account != "" {
		params["account"] = account
	}

	result, err := s.call("receive", params)
	elapsed := time.Since(start)

	if err != nil {
		s.metrics.RecordPoll(false, elapsed, 0)
		return nil, err
	}

	var messages []map[string]interface{}
	if err := json.Unmarshal(result, &messages); err != nil {
		s.metrics.RecordPoll(false, elapsed, 0)
		return nil, fmt.Errorf("unmarshal receive result: %w", err)
	}

	s.metrics.RecordPoll(true, elapsed, len(messages))
	return messages, nil
}

func (s *SignalRPC) listAccounts() error {
	start := time.Now()
	_, err := s.call("listAccounts", nil)
	s.metrics.RecordRPC("listAccounts", time.Since(start))
	return err
}

func (s *SignalRPC) send(params map[string]interface{}) (json.RawMessage, error) {
	start := time.Now()

	result, err := s.call("send", params)
	elapsed := time.Since(start)

	if err != nil {
		s.metrics.RecordRPC("send", elapsed)
		return nil, err
	}

	s.metrics.RecordRPC("send", elapsed)
	return result, nil
}

func (s *SignalRPC) readResponse() (*rpcResponse, error) {
	// signal-cli sends one newline-terminated JSON object per response;
	// the connection stays open, so we must not loop until EOF.
	line, err := s.reader.ReadBytes('\n')
	if err != nil {
		if err == io.EOF {
			return nil, fmt.Errorf("connection closed")
		}
		s.metrics.RecordTCPError("read")
		return nil, fmt.Errorf("read: %w", err)
	}

	var resp rpcResponse
	if err := json.Unmarshal(line, &resp); err != nil {
		return nil, fmt.Errorf("unmarshal response: %w", err)
	}

	return &resp, nil
}

func (s *SignalRPC) reconnectLocked() error {
	conn, err := net.DialTimeout("tcp", s.config.SignalCLIAddr(), 5*time.Second)
	if err != nil {
		s.metrics.RecordTCPError("dial")
		s.lastErr = err
		return fmt.Errorf("dial signal-cli: %w", err)
	}

	s.conn = conn
	s.reader = bufio.NewReader(conn)
	return nil
}
