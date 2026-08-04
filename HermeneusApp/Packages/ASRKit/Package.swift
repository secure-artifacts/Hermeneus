// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "ASRKit",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "ASRKit", targets: ["ASRKit"])
    ],
    dependencies: [
        .package(path: "../AudioCore")
    ],
    targets: [
        .target(
            name: "ASRKit",
            dependencies: [
                .product(name: "AudioCore", package: "AudioCore")
            ]
        )
    ]
)
