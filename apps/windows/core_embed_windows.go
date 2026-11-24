//go:build windows

package main

import "embed"

//go:embed resources/common/*.gz resources/win/*.gz
var coreFS embed.FS

const coreSubdir = "resources/win"
