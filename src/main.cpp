#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstddef>
#include <condition_variable>
#include <limits>
#include <mutex>
#include <numeric>
#include <stdexcept>
#include <string>
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

// One independently ordered direction. Python owns the background worker;
// this object publishes completed seams incrementally for responsive previews.
class SeamOrder {
public:
    SeamOrder(py::array input, const std::string& direction)
        : input_(std::move(input)), horizontal_(direction == "horizontal") {
        if (direction != "horizontal" && direction != "vertical")
            throw py::value_error("direction must be 'horizontal' or 'vertical'");
        if (!input_.dtype().is(py::dtype::of<uint8_t>()))
            throw py::type_error("image must have dtype uint8");
        if (input_.ndim() != 3 || (input_.shape(2) != 3 && input_.shape(2) != 4))
            throw py::value_error("image must have shape (height, width, 3 or 4) for RGB or RGBA");
        if (input_.shape(0) < 1 || input_.shape(1) < 1 ||
            input_.shape(0) > std::numeric_limits<int>::max() ||
            input_.shape(1) > std::numeric_limits<int>::max())
            throw py::value_error("image dimensions must be positive and fit in a 32-bit integer");
        width_ = static_cast<int>(input_.shape(1));
        height_ = static_cast<int>(input_.shape(0));
        channels_ = static_cast<int>(input_.shape(2));
        total_ = (horizontal_ ? height_ : width_) - 1;
        seam_length_ = horizontal_ ? width_ : height_;
    }

    void compute_all() {
        if (started_.exchange(true)) throw std::runtime_error("seam order calculation has already started");
        try {
            Image working = read_image(input_); // Small, one-time copy before releasing the GIL.
            {
                py::gil_scoped_release release;
                original_ = working.pixels;
                working.origins.resize(static_cast<size_t>(width_) * height_);
                std::iota(working.origins.begin(), working.origins.end(), size_t{0});
                {
                    std::lock_guard<std::mutex> guard(mutex_);
                    ordered_pixels_.resize(static_cast<size_t>(total_) * seam_length_);
                    source_ready_ = true;
                }
                condition_.notify_all();
                if (horizontal_) transpose(working);
                CarveWorkspace workspace;
                for (int count = 1; count <= total_ && !cancelled_.load(); ++count) {
                    const auto seam = find_vertical_seam(working, workspace);
                    const size_t seam_offset = static_cast<size_t>(count - 1) * seam_length_;
                    for (int row = 0; row < working.height; ++row)
                        ordered_pixels_[seam_offset + row] = working.origins[working.index(row, seam[row])];
                    {
                        std::lock_guard<std::mutex> guard(mutex_);
                        computed_ = count;
                    }
                    remove_vertical_seam(working, seam, workspace);
                    condition_.notify_all();
                }
            }
            {
                std::lock_guard<std::mutex> guard(mutex_);
                done_ = true;
            }
            condition_.notify_all();
        } catch (const std::exception& error) {
            std::lock_guard<std::mutex> guard(mutex_);
            error_ = error.what();
            done_ = true;
            condition_.notify_all();
        } catch (...) {
            std::lock_guard<std::mutex> guard(mutex_);
            error_ = "Unknown seam calculation error";
            done_ = true;
            condition_.notify_all();
        }
    }

    void cancel() {
        cancelled_.store(true);
        condition_.notify_all();
    }

    py::tuple progress() const {
        std::lock_guard<std::mutex> guard(mutex_);
        return py::make_tuple(computed_, total_, done_);
    }

    py::array_t<uint8_t> render(int count) const {
        wait_for_count(count);
        py::array_t<uint8_t> result({height_, width_, channels_});
        auto* pixels = result.mutable_data();
        {
            // Published seam positions and the source pixels are immutable, so
            // rendering can proceed without holding the worker's mutex.
            py::gil_scoped_release release;
            std::memcpy(pixels, original_.data(), original_.size());
            const size_t seam_pixels = static_cast<size_t>(count) * seam_length_;
            for (size_t i = 0; i < seam_pixels; ++i) {
                const size_t offset = ordered_pixels_[i] * channels_;
                pixels[offset] = 255;
                pixels[offset + 1] = 0;
                pixels[offset + 2] = 0;
            }
        }
        return result;
    }

    py::array_t<uint8_t> render_overlay(int count) const {
        wait_for_count(count);
        py::array_t<uint8_t> result({height_, width_, 4});
        auto* pixels = result.mutable_data();
        {
            py::gil_scoped_release release;
            std::memset(pixels, 0, static_cast<size_t>(width_) * height_ * 4);
            const size_t seam_pixels = static_cast<size_t>(count) * seam_length_;
            for (size_t i = 0; i < seam_pixels; ++i) {
                const size_t offset = ordered_pixels_[i] * 4;
                pixels[offset] = 255;
                pixels[offset + 3] = 255;
            }
        }
        return result;
    }

private:
    void wait_for_count(int count) const {
        if (count < 0 || count > total_)
            throw py::value_error("requested seam count is outside the available image dimension");
        {
            py::gil_scoped_release release;
            std::unique_lock<std::mutex> lock(mutex_);
            condition_.wait(lock, [&] { return (source_ready_ && computed_ >= count) || done_; });
        }
        std::lock_guard<std::mutex> guard(mutex_);
        if (!error_.empty()) throw std::runtime_error(error_);
        if (computed_ < count) throw std::runtime_error("seam calculation was cancelled");
    }

    py::array input_; // Retain the source without copying on the image-load path.
    int width_ = 0, height_ = 0, channels_ = 0, total_ = 0, seam_length_ = 0;
    bool horizontal_ = false;
    mutable std::mutex mutex_;
    mutable std::condition_variable condition_;
    std::atomic<bool> started_{false}, cancelled_{false};
    int computed_ = 0;
    bool done_ = false, source_ready_ = false;
    std::string error_;
    std::vector<uint8_t> original_;
    std::vector<size_t> ordered_pixels_;
};

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
    py::class_<SeamOrder, std::shared_ptr<SeamOrder>>(m, "SeamOrder")
        .def(py::init<py::array, const std::string&>(), py::arg("image"), py::arg("direction"))
        .def("compute_all", &SeamOrder::compute_all,
             "Calculate all seams incrementally; call from a background worker.")
        .def("cancel", &SeamOrder::cancel)
        .def("progress", &SeamOrder::progress)
        .def("render", &SeamOrder::render, py::arg("count"),
             "Wait for the requested seam prefix and return its original-image preview.")
        .def("render_overlay", &SeamOrder::render_overlay, py::arg("count"),
             "Wait for the requested seam prefix and return a transparent red overlay.");
}
