package main

import (
	"bytes"
	"html/template"
	"log"
	"net/http"
	"time"
)

type Templates struct {
	Index   *template.Template
	Story   *template.Template
	History *template.Template
	Login   *template.Template
}

// mediawiki style date
func FormatTimestamp(date time.Time) string {
	return date.UTC().Format("15:04, 2 January 2006 (MST)")
}

// mediawiki style date without the timezone or time
func FormatSimpleDate(date time.Time) string {
	return date.UTC().Format("2 January 2006")
}

func isShortSha(hex string) bool {
	if hex == "" || len(hex) != 7 {
		return false
	}

	for _, char := range hex {
		if !(char >= 'a' && char <= 'f') &&
			!(char >= '0' && char <= '9') {
			return false
		}
	}

	return true
}

// generic HTTP writer handler to buffer responses before sending them over the wire
// returns over the HTTP API either 500 or 200 and on Go side, returns err for failed
// template execution and failed buffer write to the HTTP writer
func renderTemplate(w http.ResponseWriter, templ *template.Template, status int, data any) error {
	// I don't want to send partial responses if the template fails
	// so we will use a buffer here to generate the response fully before we
	// send it over the wire to be either 200 or, what this gives us, 500
	var buf bytes.Buffer

	if err := templ.Execute(&buf, data); err != nil {
		log.Println("Could not render template:", err)
		http.Error(w, "Could not render template", http.StatusInternalServerError)
		return err
	}

	w.WriteHeader(status)
	_, err := buf.WriteTo(w)
	if err != nil {
		log.Println("Could not send response:", err)
		return err
	}

	return nil
}

func makeTemplates() Templates {
	funcs := template.FuncMap{
		"FormatTimestamp":  FormatTimestamp,
		"FormatSimpleDate": FormatSimpleDate,
		"ShortHash": func(s string) string {
			return s[:7]
		},
	}

	return Templates{
		Index:   template.Must(template.New("index.gohtml").Funcs(funcs).ParseFiles("templates/index.gohtml")),
		Story:   template.Must(template.New("story.gohtml").Funcs(funcs).ParseFiles("templates/story.gohtml")),
		History: template.Must(template.New("history.gohtml").Funcs(funcs).ParseFiles("templates/history.gohtml")),
		Login:   template.Must(template.New("login.gohtml").Funcs(funcs).ParseFiles("templates/login.gohtml")),
	}
}
