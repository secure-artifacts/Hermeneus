// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "TranslateKit",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "TranslateKit", targets: ["TranslateKit"])
    ],
    dependencies: [
        .package(path: "../AudioCore")
    ],
    targets: [
        .target(
            name: "TranslateKit",
            dependencies: [
                .product(name: "AudioCore", package: "AudioCore")
            ]
        )
    ]
)
