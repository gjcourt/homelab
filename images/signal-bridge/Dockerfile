FROM golang:1.23-alpine AS builder

ARG TARGETARCH=amd64

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} go build -ldflags="-s -w" -o signal-bridge .

FROM scratch

COPY --from=builder /app/signal-bridge /signal-bridge

EXPOSE 8080

ENTRYPOINT ["/signal-bridge"]
