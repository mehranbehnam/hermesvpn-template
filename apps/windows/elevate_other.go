//go:build !windows

package main

func ensureLauncherTask() {}
func removeLauncherTask() {}
func launchElevated() bool { return false }
