#!/bin/sh
sops --age=$AGE_PUBLIC \
	--encrypt \
	--encrypted-regex '^(data|stringData)$' \
	--in-place \
	$@
