package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	SignalCLIHost     string
	SignalCLIPort     int
	PollInterval      time.Duration
	HeartbeatInterval time.Duration
	ListenAddr        string
	ListenPort        int
	AllowedAccounts   []string
	AllowAll          bool
	AuthToken         string
}

func LoadConfig() Config {
	c := Config{
		SignalCLIHost:     envString("SIGNAL_CLI_HOST", "signal-cli"),
		SignalCLIPort:     envInt("SIGNAL_CLI_PORT", 7583),
		PollInterval:      envDuration("POLL_INTERVAL", 2*time.Second),
		HeartbeatInterval: envDuration("HEARTBEAT_INTERVAL", 30*time.Second),
		ListenAddr:        envString("LISTEN_ADDR", "0.0.0.0"),
		ListenPort:        envInt("LISTEN_PORT", 8080),
		AllowAll:          envBool("HERMES_ALLOW_ALL_USERS", false),
		AuthToken:         envString("HERMES_AUTH_TOKEN", ""),
	}

	if raw := os.Getenv("HERMES_ALLOWED_ACCOUNTS"); raw != "" {
		for _, a := range strings.Split(raw, ",") {
			a = strings.TrimSpace(a)
			if a != "" {
				c.AllowedAccounts = append(c.AllowedAccounts, a)
			}
		}
	}

	return c
}

func (c Config) IsAccountAllowed(account string) bool {
	if c.AllowAll {
		return true
	}
	for _, a := range c.AllowedAccounts {
		if a == account {
			return true
		}
	}
	return false
}

func (c Config) CheckBearer(authHeader string) bool {
	if c.AuthToken == "" {
		return true
	}
	return authHeader == "Bearer "+c.AuthToken
}

func (c Config) SignalCLIAddr() string {
	return fmt.Sprintf("%s:%d", c.SignalCLIHost, c.SignalCLIPort)
}

func envString(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	v, err := strconv.Atoi(os.Getenv(key))
	if err != nil {
		return def
	}
	return v
}

func envBool(key string, def bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return def
	}
	return b
}

func envDuration(key string, def time.Duration) time.Duration {
	v, err := time.ParseDuration(os.Getenv(key))
	if err != nil {
		return def
	}
	return v
}
