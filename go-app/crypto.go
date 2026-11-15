package main

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"strings"

	"golang.org/x/crypto/argon2"
)

func HashSecret(secret string) (string, error) {
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}

	hash := argon2.IDKey(
		[]byte(secret),
		salt,
		2,
		19*1024,
		1,
		32,
	)

	enc := base64.RawStdEncoding

	return enc.EncodeToString(salt) + ":" +
		enc.EncodeToString(hash), nil
}

func VerifySecret(secret, stored string) bool {
	saltStr, hashStr, ok := strings.Cut(stored, ":")
	if !ok {
		return false
	}

	enc := base64.RawStdEncoding

	salt, err := enc.DecodeString(saltStr)
	if err != nil || len(salt) != 16 {
		return false
	}

	expected, err := enc.DecodeString(hashStr)
	if err != nil || len(expected) != 32 {
		return false
	}

	actual := argon2.IDKey(
		[]byte(secret),
		salt,
		2,
		19*1024,
		1,
		32,
	)

	return subtle.ConstantTimeCompare(actual, expected) == 1
}
