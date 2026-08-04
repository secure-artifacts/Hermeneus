// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AudioCore",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "AudioCore", targets: ["AudioCore"])
    ],
    targets: [
        .target(name: "AudioCore", dependencies: [])
    ]
)
