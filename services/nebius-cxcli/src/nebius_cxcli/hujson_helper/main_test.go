package main

import (
	"strings"
	"testing"
)

func TestPreservesUnrelatedPolicy(t *testing.T) {
	output, err := transform([]byte(`{"document":"{ // retained\n\"unknown\": {\"future\":true,},\n\"grants\": [],\n}","patch":[{"op":"add","path":"/grants/-","value":{"src":["group:dev"],"dst":["10.0.0.0/24"],"ip":["tcp:443"]}}]}`))
	if err != nil || !strings.Contains(string(output), "// retained") || !strings.Contains(string(output), "future") {
		t.Fatalf("lossless patch failed: %v", err)
	}
}

func TestRejectsInvalidPatch(t *testing.T) {
	if _, err := transform([]byte(`{"document":"{}","patch":[{"op":"remove","path":"/absent"}]}`)); err == nil {
		t.Fatal("invalid patch accepted")
	}
}

func TestRejectsDuplicatePolicyKeys(t *testing.T) {
	if _, err := transform([]byte(`{"document":"{\"grants\":[],\"grants\":[]}","patch":[]}`)); err == nil {
		t.Fatal("duplicate keys accepted")
	}
}
func TestPreservesLargeNumbers(t *testing.T) {
	output, err := transform([]byte(`{"document":"{\"unknown\":9007199254740993}","patch":[]}`))
	if err != nil || !strings.Contains(string(output), "9007199254740993") {
		t.Fatal("numeric precision lost")
	}
}
