[app]
title = MindFlow
package.name = mindflow
package.domain = org.mindflow
source.dir = .
source.include_exts = py,png,jpg,jpeg,json,mflow,txt
source.exclude_dirs = .git,.github,__pycache__,bin,.buildozer
version = 0.1.0
requirements = python3,kivy,plyer
orientation = landscape
fullscreen = 0

# Android: modern 64-bit phones/tablets.
android.archs = arm64-v8a
android.api = 35
android.minapi = 24
android.permissions = INTERNET
android.accept_sdk_license = True

# SDL2 is the normal Kivy Android bootstrap.
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
