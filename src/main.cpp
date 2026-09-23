#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstddef>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace py = pybind11;

namespace {
struct Image {
    int width, height, channels;
    std::vector<uint8_t> pixels;
    // Original pixel indices travel with the pixels during carving/transposition.
    std::vector<size_t> origins;

    size_t index(int row, int col) const {
        return static_cast<size_t>(row) * width + col;
    }
    size_t offset(int row, int col) const { return index(row, col) * channels; }
};

struct CarveWorkspace {
    std::vector<uint64_t> previous_cost, current_cost;
    std::vector<int8_t> predecessor;
    std::vector<uint8_t> pixel_scratch;
    std::vector<size_t> origin_scratch;
};

int target_dimension(const py::object& value, int maximum, const char* name) {
    if (PyBool_Check(value.ptr()) || !PyIndex_Check(value.ptr())) {
        throw py::type_error(std::string(name) + " must be an integer (not a boolean)");
    }
    auto integer = py::reinterpret_steal<py::object>(PyNumber_Index(value.ptr()));
    if (!integer) throw py::error_already_set();
    const auto number = PyLong_AsLongLong(integer.ptr());
    if (PyErr_Occurred()) {
        PyErr_Clear();
        throw py::value_error(std::string(name) + " is outside the supported range");
    }
    if (number < 1 || number > maximum) {
        throw py::value_error(std::string(name) + " must be between 1 and the original dimension; enlargement is unsupported");
    }
    return static_cast<int>(number);
}

Image read_image(const py::array& input) {
    if (!input.dtype().is(py::dtype::of<uint8_t>()))
        throw py::type_error("image must have dtype uint8");
    if (input.ndim() != 3 || (input.shape(2) != 3 && input.shape(2) != 4))
        throw py::value_error("image must have shape (height, width, 3 or 4) for RGB or RGBA");
    const auto h = input.shape(0), w = input.shape(1), c = input.shape(2);
    if (h < 1 || w < 1 || h > std::numeric_limits<int>::max() || w > std::numeric_limits<int>::max())
        throw py::value_error("image dimensions must be positive and fit in a 32-bit integer");
    const auto max_size = static_cast<size_t>(std::numeric_limits<py::ssize_t>::max());
    if (static_cast<size_t>(h) > max_size / static_cast<size_t>(w) / static_cast<size_t>(c))
        throw py::value_error("image is too large");
    // Normalize strides, including negative, broadcast and Fortran-order views.
    auto contiguous = py::array_t<uint8_t, py::array::c_style>::ensure(input);
    if (!contiguous) throw py::value_error("could not read image as a contiguous array");
    const size_t count = static_cast<size_t>(h) * w * c;
    return {static_cast<int>(w), static_cast<int>(h), static_cast<int>(c),
            std::vector<uint8_t>(contiguous.data(), contiguous.data() + count), {}};
}

std::vector<int> find_vertical_seam(const Image& image, CarveWorkspace& workspace) {
    const int w = image.width, h = image.height;
    // Keep only two rows of cumulative costs and one byte of backtracking data
    // per pixel, rather than a 64-bit cumulative-cost image per seam.
    workspace.previous_cost.resize(w);
    workspace.current_cost.resize(w);
    workspace.predecessor.resize(static_cast<size_t>(w) * h);
    auto& previous_cost = workspace.previous_cost;
    auto& current_cost = workspace.current_cost;
    auto& predecessor = workspace.predecessor;
    for (int row = 0; row < h; ++row) {
        const size_t row_offset = static_cast<size_t>(row) * w;
        for (int col = 0; col < w; ++col) {
            uint64_t energy = 0;
            const auto left = image.offset(row, std::max(0, col - 1));
            const auto right = image.offset(row, std::min(w - 1, col + 1));
            const auto up = image.offset(std::max(0, row - 1), col);
            const auto down = image.offset(std::min(h - 1, row + 1), col);
            for (int c = 0; c < 3; ++c) {
                energy += std::abs(int(image.pixels[left + c]) - int(image.pixels[right + c]));
                energy += std::abs(int(image.pixels[up + c]) - int(image.pixels[down + c]));
            }
            if (row == 0) {
                current_cost[col] = energy;
            } else {
                uint64_t best = std::numeric_limits<uint64_t>::max();
                int best_col = std::max(0, col - 1);
                for (int prev = best_col; prev <= std::min(w - 1, col + 1); ++prev) {
                    if (previous_cost[prev] < best) {
                        best = previous_cost[prev];
                        best_col = prev;
                    }
                }
                current_cost[col] = energy + best;
                predecessor[row_offset + col] = static_cast<int8_t>(best_col - col);
            }
        }
        previous_cost.swap(current_cost);
    }
    std::vector<int> seam(h);
    seam[h - 1] = static_cast<int>(std::min_element(previous_cost.begin(), previous_cost.end()) - previous_cost.begin());
    for (int row = h - 2; row >= 0; --row) {
        const int col = seam[row + 1];
        seam[row] = col + predecessor[static_cast<size_t>(row + 1) * w + col];
    }
    return seam;
}

void remove_vertical_seam(Image& image, const std::vector<int>& seam, CarveWorkspace& workspace) {
    if (image.width <= 1 || seam.size() != static_cast<size_t>(image.height))
        throw std::invalid_argument("invalid seam dimensions");
    for (int row = 0; row < image.height; ++row) {
        if (seam[row] < 0 || seam[row] >= image.width ||
            (row > 0 && std::abs(seam[row] - seam[row - 1]) > 1))
            throw std::invalid_argument("invalid seam coordinates");
    }
    const int new_width = image.width - 1;
    workspace.pixel_scratch.resize(static_cast<size_t>(new_width) * image.height * image.channels);
    if (!image.origins.empty()) workspace.origin_scratch.resize(static_cast<size_t>(new_width) * image.height);
    for (int row = 0; row < image.height; ++row) {
        const size_t source_pixel = image.index(row, 0);
        const size_t target_pixel = static_cast<size_t>(row) * new_width;
        const size_t left_pixels = static_cast<size_t>(seam[row]);
        const size_t right_pixels = static_cast<size_t>(image.width - seam[row] - 1);
        const size_t left_bytes = left_pixels * image.channels;
        const size_t right_bytes = right_pixels * image.channels;
        if (left_bytes) {
            std::memcpy(workspace.pixel_scratch.data() + target_pixel * image.channels,
                        image.pixels.data() + source_pixel * image.channels, left_bytes);
            if (!image.origins.empty())
                std::memcpy(workspace.origin_scratch.data() + target_pixel,
                            image.origins.data() + source_pixel, left_pixels * sizeof(size_t));
        }
        if (right_bytes) {
            std::memcpy(workspace.pixel_scratch.data() + (target_pixel + left_pixels) * image.channels,
                        image.pixels.data() + (source_pixel + left_pixels + 1) * image.channels, right_bytes);
            if (!image.origins.empty())
                std::memcpy(workspace.origin_scratch.data() + target_pixel + left_pixels,
                            image.origins.data() + source_pixel + left_pixels + 1, right_pixels * sizeof(size_t));
        }
    }
    image.pixels.swap(workspace.pixel_scratch);
    if (!image.origins.empty()) image.origins.swap(workspace.origin_scratch);
    image.width = new_width;
}

void transpose(Image& image) {
    Image result{image.height, image.width, image.channels, {}, {}};
    result.pixels.resize(image.pixels.size());
    result.origins.resize(image.origins.size());
    for (int row = 0; row < image.height; ++row) {
        for (int col = 0; col < image.width; ++col) {
            std::copy_n(image.pixels.data() + image.offset(row, col), image.channels,
                        result.pixels.data() + result.offset(col, row));
            if (!image.origins.empty()) result.origins[result.index(col, row)] = image.origins[image.index(row, col)];
        }
    }
    image = std::move(result);
}

void carve_width(Image& working, int target, std::vector<uint8_t>* marked, CarveWorkspace& workspace) {
    while (working.width > target) {
        const auto seam = find_vertical_seam(working, workspace);
        if (marked) {
            for (int row = 0; row < working.height; ++row) {
                const auto offset = working.origins[working.index(row, seam[row])] * working.channels;
                (*marked)[offset] = 255;
                (*marked)[offset + 1] = 0;
                (*marked)[offset + 2] = 0;
            }
        }
        remove_vertical_seam(working, seam, workspace);
    }
}

py::array_t<uint8_t> process(const py::array& input, const py::object& width,
                           const py::object& height, bool highlight) {
    Image working = read_image(input);
    const int new_width = target_dimension(width, working.width, "new_width");
    const int new_height = target_dimension(height, working.height, "new_height");
    const int original_width = working.width, original_height = working.height;
    std::vector<uint8_t> marked;
    CarveWorkspace workspace;
    {
        // The input has been copied: Python callers may keep using their arrays.
        py::gil_scoped_release release;
        if (highlight) {
            marked = working.pixels;
            working.origins.resize(static_cast<size_t>(working.width) * working.height);
            std::iota(working.origins.begin(), working.origins.end(), size_t{0});
        }
        carve_width(working, new_width, highlight ? &marked : nullptr, workspace);
        if (working.height != new_height) {
            transpose(working);
            carve_width(working, new_height, highlight ? &marked : nullptr, workspace);
            transpose(working);
        }
    }
    const int result_height = highlight ? original_height : working.height;
    const int result_width = highlight ? original_width : working.width;
    py::array_t<uint8_t> result({result_height, result_width, working.channels});
    const auto& pixels = highlight ? marked : working.pixels;
    std::memcpy(result.mutable_data(), pixels.data(), pixels.size());
    return result;
}
} // namespace

PYBIND11_MODULE(main, m) {
    m.doc() = "RGB/RGBA uint8 seam carving: width first, then height; shrink only.";
    m.def("highlight", [](const py::array& image, const py::object& width, const py::object& height) {
        return process(image, width, height, true);
    }, py::arg("input_image").noconvert(), py::arg("new_width"), py::arg("new_height"),
    "Mark removed pixels red at their original positions; preserve input dimensions and alpha.");
    m.def("modify", [](const py::array& image, const py::object& width, const py::object& height) {
        return process(image, width, height, false);
    }, py::arg("input_image").noconvert(), py::arg("new_width"), py::arg("new_height"),
    "Remove minimum-energy vertical seams, then horizontal seams, to the requested size.");
}
