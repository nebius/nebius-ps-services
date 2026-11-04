// A bounded, stdin-only adapter around Tailscale's lossless policy parser.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"

	"github.com/tailscale/hujson"
)

// Reject duplicate keys rather than letting different parsers choose different grants.
func uniqueKeys(decoder *json.Decoder, depth int) error {
	if depth > 128 {
		return errors.New("policy nesting limit")
	}
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, container := token.(json.Delim)
	if !container {
		return nil
	}
	if delimiter == '{' {
		seen := map[string]bool{}
		for decoder.More() {
			token, err := decoder.Token()
			if err != nil {
				return err
			}
			key, ok := token.(string)
			if !ok || seen[key] {
				return errors.New("duplicate policy key")
			}
			seen[key] = true
			if err := uniqueKeys(decoder, depth+1); err != nil {
				return err
			}
		}
	} else if delimiter == '[' {
		for decoder.More() {
			if err := uniqueKeys(decoder, depth+1); err != nil {
				return err
			}
		}
	} else {
		return errors.New("unexpected delimiter")
	}
	_, err = decoder.Token()
	return err
}

func transform(input []byte) ([]byte, error) {
	var request struct {
		Document string          `json:"document"`
		Patch    json.RawMessage `json:"patch"`
	}
	if err := json.Unmarshal(input, &request); err != nil {
		return nil, err
	}
	value, err := hujson.Parse([]byte(request.Document))
	if err != nil {
		return nil, err
	}
	initial := value.Clone()
	initial.Standardize()
	if err := uniqueKeys(json.NewDecoder(bytes.NewReader(initial.Pack())), 0); err != nil {
		return nil, err
	}
	if len(request.Patch) != 0 && string(request.Patch) != "null" {
		if err := value.Patch(request.Patch); err != nil {
			return nil, err
		}
	}
	cloned := value.Clone()
	cloned.Standardize()
	var semantic any
	decoder := json.NewDecoder(bytes.NewReader(cloned.Pack()))
	decoder.UseNumber()
	if err := decoder.Decode(&semantic); err != nil {
		return nil, err
	}
	return json.Marshal(map[string]any{"document": string(value.Pack()), "value": semantic})
}

func main() {
	input, err := io.ReadAll(io.LimitReader(os.Stdin, 8*1024*1024+1))
	if err != nil || len(input) > 8*1024*1024 {
		fmt.Fprintln(os.Stderr, "invalid policy input")
		os.Exit(1)
	}
	output, err := transform(input)
	if err != nil {
		fmt.Fprintln(os.Stderr, "policy parsing or patch validation failed")
		os.Exit(1)
	}
	_, _ = os.Stdout.Write(output)
}
