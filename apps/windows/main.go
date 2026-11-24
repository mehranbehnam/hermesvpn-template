//go:build !hvtest

package main

import (
	"embed"
	"os"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
	"github.com/wailsapp/wails/v2/pkg/options/windows"
)

//go:embed all:frontend/dist
var assets embed.FS

func main() {
	// Started without admin rights (normal double-click — the exe no longer
	// demands elevation, so its icon has no UAC shield): hand over to the
	// elevated launcher task and exit. "--no-elevate" skips this.
	if isWindows && !isAdmin() && !hasArg("--no-elevate") {
		savePendingLink(os.Args[1:])
		if launchElevated() {
			return
		}
	}
	app := NewApp()
	err := wails.Run(&options.App{
		Title:            windowTitle,
		Width:            1180,
		Height:           740,
		MinWidth:         900,
		MinHeight:        600,
		BackgroundColour: &options.RGBA{R: 5, G: 11, B: 20, A: 255},
		AssetServer:      &assetserver.Options{Assets: assets},
		OnStartup:        app.startup,
		OnDomReady:       app.domReady,
		OnShutdown:       app.shutdown,
		SingleInstanceLock: &options.SingleInstanceLock{
			UniqueId:               "hermesvpn-connect-7c1e2f",
			OnSecondInstanceLaunch: func(d options.SecondInstanceData) { app.onSecondInstance(d.Args) },
		},
		Bind: []interface{}{app},
		Windows: &windows.Options{
			WebviewIsTransparent: false,
			WindowIsTranslucent:  false,
			Theme:                windows.Dark,
		},
	})
	if err != nil {
		println("Error:", err.Error())
	}
}
