// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "VADKit",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "VADKit", targets: ["VADKit"])
    ],
    dependencies: [
        .package(path: "../AudioCore")
    ],
    targets: [
        .target(
            name: "VADKit",
            dependencies: [
                .product(name: "AudioCore", package: "AudioCore")
            ]
        )
    ]
)
