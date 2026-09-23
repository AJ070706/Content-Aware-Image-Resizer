#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
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

std::vector<int> find_vertical_seam(const Image& image) {
    const int w = image.width, h = image.height;
    // A path can exceed 32-bit cost even though a single pixel cannot.
    std::vector<uint64_t> cost(static_cast<size_t>(w) * h);
    for (int row = 0; row < h; ++row) {
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
            if (row > 0) {
                uint64_t best = std::numeric_limits<uint64_t>::max();
                for (int prev = std::max(0, col - 1); prev <= std::min(w - 1, col + 1); ++prev)
                    best = std::min(best, cost[image.index(row - 1, prev)]);
                energy += best;
            }
            cost[image.index(row, col)] = energy;
        }
    }
    std::vector<int> seam(h);
    auto last = cost.begin() + static_cast<size_t>(h - 1) * w;
    seam[h - 1] = static_cast<int>(std::min_element(last, last + w) - last);
    for (int row = h - 2; row >= 0; --row) {
        const int previous = seam[row + 1];
        int best = std::max(0, previous - 1);
        for (int col = best + 1; col <= std::min(w - 1, previous + 1); ++col) {
            if (cost[image.index(row, col)] < cost[image.index(row, best)]) best = col;
        }
        seam[row] = best; // Ties choose the leftmost predecessor.
    }
    return seam;
}

void remove_vertical_seam(Image& image, const std::vector<int>& seam) {
    if (image.width <= 1 || seam.size() != static_cast<size_t>(image.height))
        throw std::invalid_argument("invalid seam dimensions");
    for (int row = 0; row < image.height; ++row) {
        if (seam[row] < 0 || seam[row] >= image.width ||
            (row > 0 && std::abs(seam[row] - seam[row - 1]) > 1))
            throw std::invalid_argument("invalid seam coordinates");
    }
    Image next{image.width - 1, image.height, image.channels, {}, {}};
    next.pixels.resize(static_cast<size_t>(next.width) * next.height * next.channels);
    if (!image.origins.empty()) next.origins.resize(static_cast<size_t>(next.width) * next.height);
    for (int row = 0; row < image.height; ++row) {
        for (int col = 0; col < next.width; ++col) {
            const int old_col = col < seam[row] ? col : col + 1;
            std::copy_n(image.pixels.data() + image.offset(row, old_col), image.channels,
                        next.pixels.data() + next.offset(row, col));
            if (!image.origins.empty()) next.origins[next.index(row, col)] = image.origins[image.index(row, old_col)];
        }
    }
    image = std::move(next); // Update width exactly once.
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

void carve_width(Image& working, int target, std::vector<uint8_t>* marked) {
    while (working.width > target) {
        const auto seam = find_vertical_seam(working);
        if (marked) {
            for (int row = 0; row < working.height; ++row) {
                const auto offset = working.origins[working.index(row, seam[row])] * working.channels;
                (*marked)[offset] = 255;
                (*marked)[offset + 1] = 0;
                (*marked)[offset + 2] = 0;
            }
        }
        remove_vertical_seam(working, seam);
    }
}

py::array_t<uint8_t> process(const py::array& input, const py::object& width,
                           const py::object& height, bool highlight) {
    Image working = read_image(input);
    const int new_width = target_dimension(width, working.width, "new_width");
    const int new_height = target_dimension(height, working.height, "new_height");
    const int original_width = working.width, original_height = working.height;
    std::vector<uint8_t> marked;
    {
        // The input has been copied: Python callers may keep using their arrays.
        py::gil_scoped_release release;
        if (highlight) {
            marked = working.pixels;
            working.origins.resize(static_cast<size_t>(working.width) * working.height);
            std::iota(working.origins.begin(), working.origins.end(), size_t{0});
        }
        carve_width(working, new_width, highlight ? &marked : nullptr);
        if (working.height != new_height) {
            transpose(working);
            carve_width(working, new_height, highlight ? &marked : nullptr);
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
