// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "TTSKit",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "TTSKit", targets: ["TTSKit"])
    ],
    dependencies: [
        .package(path: "../AudioCore")
    ],
    targets: [
        .target(
            name: "TTSKit",
            dependencies: [
                .product(name: "AudioCore", package: "AudioCore")
            ]
        )
    ]
)
