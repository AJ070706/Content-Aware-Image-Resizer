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

// Backward or forward-energy seam carving. A seam is recomputed after each removal;
// SeamOrder publishes the original-coordinate removal order for live previews.
namespace py = pybind11;

namespace {
enum class EnergyMode { Backward, Forward };

EnergyMode parse_energy_mode(const std::string& mode) {
    if (mode == "backward") return EnergyMode::Backward;
    if (mode == "forward") return EnergyMode::Forward;
    throw py::value_error("energy must be 'backward' or 'forward'");
}

struct Image {
    int width, height, channels;
    std::vector<uint8_t> pixels;
    // Original pixel indices travel with the pixels during carving/transposition.
    std::vector<size_t> origins;
    std::vector<int8_t> mask; // +1 protects a pixel; -1 favors its removal.

    size_t index(int row, int col) const {
        return static_cast<size_t>(row) * width + col;
    }
    size_t offset(int row, int col) const { return index(row, col) * channels; }
};

struct CarveWorkspace {
    // Scratch buffers are reused across seams to avoid repeated large allocations.
    std::vector<int64_t> previous_cost, current_cost;
    std::vector<int8_t> predecessor;
    std::vector<uint8_t> pixel_scratch;
    std::vector<size_t> origin_scratch;
    std::vector<int8_t> mask_scratch;
};

int target_dimension(const py::object& value, int maximum, const char* name) {
    // A single insertion pass can add each original pixel at most once.
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
    if (number < 1 || number > static_cast<int64_t>(maximum) * 2 ||
        number > std::numeric_limits<int>::max()) {
        throw py::value_error(std::string(name) + " must be between 1 and twice the original dimension");
    }
    return static_cast<int>(number);
}

Image read_image(const py::array& input) {
    // Accept noncontiguous uint8 RGB/RGBA arrays and take one owned copy.
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
            std::vector<uint8_t>(contiguous.data(), contiguous.data() + count), {}, {}};
}

std::vector<int8_t> read_mask(const py::object& input, int width, int height) {
    if (input.is_none()) return {};
    auto array = py::cast<py::array>(input);
    if (!array.dtype().is(py::dtype::of<int8_t>()))
        throw py::type_error("mask must have dtype int8");
    if (array.ndim() != 2 || array.shape(0) != height || array.shape(1) != width)
        throw py::value_error("mask must match image height and width");
    auto contiguous = py::array_t<int8_t, py::array::c_style>::ensure(array);
    if (!contiguous) throw py::value_error("could not read mask as a contiguous array");
    const size_t count = static_cast<size_t>(height) * width;
    std::vector<int8_t> mask(contiguous.data(), contiguous.data() + count);
    if (std::any_of(mask.begin(), mask.end(), [](int8_t value) { return value < -1 || value > 1; }))
        throw py::value_error("mask values must be -1, 0, or 1");
    return mask;
}

std::vector<int> find_vertical_seam(const Image& image, CarveWorkspace& workspace, EnergyMode mode) {
    // Dynamic programming stores two cost rows and a predecessor step per pixel.
    // Left-to-right traversal makes equal-cost choices deterministic.
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
            int64_t energy = 0;
            int64_t left_turn = 0, right_turn = 0;
            const auto left = image.offset(row, std::max(0, col - 1));
            const auto right = image.offset(row, std::min(w - 1, col + 1));
            const auto up = image.offset(std::max(0, row - 1), col);
            const auto down = image.offset(std::min(h - 1, row + 1), col);
            for (int c = 0; c < 3; ++c) {
                energy += std::abs(int(image.pixels[left + c]) - int(image.pixels[right + c]));
                if (mode == EnergyMode::Backward)
                    energy += std::abs(int(image.pixels[up + c]) - int(image.pixels[down + c]));
                else if (row > 0) {
                    // The extra forward costs represent edges created by a
                    // diagonal seam step when this pixel is removed.
                    left_turn += std::abs(int(image.pixels[up + c]) - int(image.pixels[left + c]));
                    right_turn += std::abs(int(image.pixels[up + c]) - int(image.pixels[right + c]));
                }
            }
            if (!image.mask.empty()) {
                const int8_t mark = image.mask[row_offset + col];
                energy += mark > 0 ? 10000000 : mark < 0 ? -1000000 : 0;
            }
            if (row == 0) {
                current_cost[col] = energy;
            } else {
                int64_t best = std::numeric_limits<int64_t>::max();
                int best_col = std::max(0, col - 1);
                for (int prev = best_col; prev <= std::min(w - 1, col + 1); ++prev) {
                    const int64_t transition = mode == EnergyMode::Forward
                        ? (prev < col ? left_turn : prev > col ? right_turn : 0) : 0;
                    const int64_t candidate = previous_cost[prev] + transition;
                    if (candidate < best) {
                        best = candidate;
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
    // Compact each row; move original indices alongside colors when tracking them.
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
    if (!image.mask.empty()) workspace.mask_scratch.resize(static_cast<size_t>(new_width) * image.height);
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
            if (!image.mask.empty())
                std::memcpy(workspace.mask_scratch.data() + target_pixel,
                            image.mask.data() + source_pixel, left_pixels);
        }
        if (right_bytes) {
            std::memcpy(workspace.pixel_scratch.data() + (target_pixel + left_pixels) * image.channels,
                        image.pixels.data() + (source_pixel + left_pixels + 1) * image.channels, right_bytes);
            if (!image.origins.empty())
                std::memcpy(workspace.origin_scratch.data() + target_pixel + left_pixels,
                            image.origins.data() + source_pixel + left_pixels + 1, right_pixels * sizeof(size_t));
            if (!image.mask.empty())
                std::memcpy(workspace.mask_scratch.data() + target_pixel + left_pixels,
                            image.mask.data() + source_pixel + left_pixels + 1, right_pixels);
        }
    }
    image.pixels.swap(workspace.pixel_scratch);
    if (!image.origins.empty()) image.origins.swap(workspace.origin_scratch);
    if (!image.mask.empty()) image.mask.swap(workspace.mask_scratch);
    image.width = new_width;
}

void transpose(Image& image) {
    // Reuse the vertical algorithm for horizontal seams without changing its tie rule.
    Image result{image.height, image.width, image.channels, {}, {}, {}};
    result.pixels.resize(image.pixels.size());
    result.origins.resize(image.origins.size());
    result.mask.resize(image.mask.size());
    for (int row = 0; row < image.height; ++row) {
        for (int col = 0; col < image.width; ++col) {
            std::copy_n(image.pixels.data() + image.offset(row, col), image.channels,
                        result.pixels.data() + result.offset(col, row));
            if (!image.origins.empty()) result.origins[result.index(col, row)] = image.origins[image.index(row, col)];
            if (!image.mask.empty()) result.mask[result.index(col, row)] = image.mask[image.index(row, col)];
        }
    }
    image = std::move(result);
}

void carve_width(Image& working, int target, std::vector<uint8_t>* marked,
                 CarveWorkspace& workspace, EnergyMode mode) {
    // Recompute energy on the smaller image after every removal.
    while (working.width > target) {
        const auto seam = find_vertical_seam(working, workspace, mode);
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

void insert_selected(Image& image, const std::vector<uint8_t>& selected, int count) {
    // Insert a blended pixel beside each selected original pixel. At the last
    // column use the left neighbor; a one-column image has no distinct neighbor.
    const int old_width = image.width;
    const int new_width = old_width + count;
    Image enlarged{new_width, image.height, image.channels, {}, {}, {}};
    enlarged.pixels.resize(static_cast<size_t>(new_width) * image.height * image.channels);
    if (!image.origins.empty()) enlarged.origins.resize(static_cast<size_t>(new_width) * image.height);
    if (!image.mask.empty()) enlarged.mask.resize(static_cast<size_t>(new_width) * image.height);
    for (int row = 0; row < image.height; ++row) {
        int output_col = 0;
        for (int col = 0; col < old_width; ++col) {
            const size_t source = image.index(row, col);
            const size_t destination = enlarged.index(row, output_col++);
            std::copy_n(image.pixels.data() + source * image.channels, image.channels,
                        enlarged.pixels.data() + destination * image.channels);
            if (!image.origins.empty()) enlarged.origins[destination] = image.origins[source];
            if (!image.mask.empty()) enlarged.mask[destination] = image.mask[source];
            if (!selected[source]) continue;
            const int neighbor_col = old_width == 1 ? 0 : col + 1 < old_width ? col + 1 : col - 1;
            const size_t neighbor = image.index(row, neighbor_col);
            const size_t inserted = enlarged.index(row, output_col++);
            for (int channel = 0; channel < image.channels; ++channel)
                enlarged.pixels[inserted * image.channels + channel] = static_cast<uint8_t>(
                    (int(image.pixels[source * image.channels + channel]) +
                     int(image.pixels[neighbor * image.channels + channel])) / 2);
            if (!image.origins.empty()) enlarged.origins[inserted] = image.origins[source];
            if (!image.mask.empty()) enlarged.mask[inserted] = image.mask[source];
        }
    }
    image = std::move(enlarged);
}

void enlarge_width(Image& image, int target, std::vector<uint8_t>* marked, EnergyMode mode) {
    // Find non-overlapping seams on a shrinking copy, then insert all of them
    // into the untouched image. This avoids repeatedly selecting the same seam.
    const int count = target - image.width;
    Image search = image;
    search.origins.resize(static_cast<size_t>(image.width) * image.height);
    std::iota(search.origins.begin(), search.origins.end(), size_t{0});
    std::vector<uint8_t> selected(search.origins.size(), 0);
    CarveWorkspace workspace;
    for (int seam_number = 0; seam_number < count && search.width > 1; ++seam_number) {
        const auto seam = find_vertical_seam(search, workspace, mode);
        for (int row = 0; row < search.height; ++row)
            selected[search.origins[search.index(row, seam[row])]] = 1;
        remove_vertical_seam(search, seam, workspace);
    }
    if (count == image.width) {
        for (size_t origin : search.origins) selected[origin] = 1;
    }
    if (marked) {
        for (size_t source = 0; source < selected.size(); ++source) {
            if (!selected[source]) continue;
            const size_t offset = image.origins[source] * image.channels;
            (*marked)[offset] = 255;
            (*marked)[offset + 1] = 0;
            (*marked)[offset + 2] = 0;
        }
    }
    insert_selected(image, selected, count);
}

// One independently ordered direction. Python owns the background worker;
// this object publishes completed, immutable seam prefixes for concurrent readers.
class SeamOrder {
public:
    SeamOrder(py::array input, const std::string& direction, py::object mask, const std::string& energy)
        : input_(std::move(input)), mask_input_(std::move(mask)),
          mode_(parse_energy_mode(energy)), horizontal_(direction == "horizontal") {
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
        if (!mask_input_.is_none()) {
            auto array = py::cast<py::array>(mask_input_);
            if (!array.dtype().is(py::dtype::of<int8_t>()))
                throw py::type_error("mask must have dtype int8");
            if (array.ndim() != 2 || array.shape(0) != height_ || array.shape(1) != width_)
                throw py::value_error("mask must match image height and width");
        }
    }

    void compute_all() {
        // Publish each seam only after all of its original positions are written.
        if (started_.exchange(true)) throw std::runtime_error("seam order calculation has already started");
        try {
            Image working = read_image(input_); // Small, one-time copy before releasing the GIL.
            working.mask = read_mask(mask_input_, width_, height_);
            {
                py::gil_scoped_release release;
                original_ = working.pixels;
                working.origins.resize(static_cast<size_t>(width_) * height_);
                std::iota(working.origins.begin(), working.origins.end(), size_t{0});
                {
                    std::lock_guard<std::mutex> guard(mutex_);
                    ordered_pixels_.resize(static_cast<size_t>(total_ + 1) * seam_length_);
                    source_ready_ = true;
                }
                condition_.notify_all();
                if (horizontal_) transpose(working);
                CarveWorkspace workspace;
                for (int count = 1; count <= total_ && !cancelled_.load(); ++count) {
                    const auto seam = find_vertical_seam(working, workspace, mode_);
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
                if (!cancelled_.load()) {
                    // The final one-column image supplies the last distinct
                    // insertion path, allowing an exact 2x enlargement.
                    const size_t offset = static_cast<size_t>(total_) * seam_length_;
                    for (int row = 0; row < working.height; ++row)
                        ordered_pixels_[offset + row] = working.origins[working.index(row, 0)];
                    {
                        std::lock_guard<std::mutex> guard(mutex_);
                        insertion_ready_ = true;
                    }
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

    py::array_t<uint8_t> render_insertion(int count) const {
        wait_for_insertion_count(count);
        py::array_t<uint8_t> result({height_, width_, channels_});
        auto* pixels = result.mutable_data();
        {
            py::gil_scoped_release release;
            std::memcpy(pixels, original_.data(), original_.size());
            for (size_t i = 0; i < static_cast<size_t>(count) * seam_length_; ++i) {
                const size_t offset = ordered_pixels_[i] * channels_;
                pixels[offset] = 255;
                pixels[offset + 1] = 0;
                pixels[offset + 2] = 0;
            }
        }
        return result;
    }

    py::array_t<uint8_t> render_insertion_overlay(int count) const {
        wait_for_insertion_count(count);
        py::array_t<uint8_t> result({height_, width_, 4});
        auto* pixels = result.mutable_data();
        {
            py::gil_scoped_release release;
            std::memset(pixels, 0, static_cast<size_t>(width_) * height_ * 4);
            for (size_t i = 0; i < static_cast<size_t>(count) * seam_length_; ++i) {
                const size_t offset = ordered_pixels_[i] * 4;
                pixels[offset] = 255;
                pixels[offset + 3] = 255;
            }
        }
        return result;
    }

    py::array_t<uint8_t> render_modified(int count) const {
        // Skip the first count removed original pixels, preserving the order of survivors.
        wait_for_count(count);
        const int output_width = width_ - (horizontal_ ? 0 : count);
        const int output_height = height_ - (horizontal_ ? count : 0);
        py::array_t<uint8_t> result({output_height, output_width, channels_});
        auto* pixels = result.mutable_data();
        {
            py::gil_scoped_release release;
            const size_t source_pixels = static_cast<size_t>(width_) * height_;
            std::vector<uint8_t> removed(source_pixels, 0);
            for (size_t i = 0; i < static_cast<size_t>(count) * seam_length_; ++i)
                removed[ordered_pixels_[i]] = 1;
            if (!horizontal_) {
                size_t destination = 0;
                for (size_t source = 0; source < source_pixels; ++source) {
                    if (!removed[source]) {
                        std::memcpy(pixels + destination * channels_,
                                    original_.data() + source * channels_, channels_);
                        ++destination;
                    }
                }
            } else {
                std::vector<int> destination_row(width_, 0);
                for (int row = 0; row < height_; ++row) {
                    for (int col = 0; col < width_; ++col) {
                        const size_t source = static_cast<size_t>(row) * width_ + col;
                        if (!removed[source]) {
                            const size_t destination = static_cast<size_t>(destination_row[col]++) * width_ + col;
                            std::memcpy(pixels + destination * channels_,
                                        original_.data() + source * channels_, channels_);
                        }
                    }
                }
            }
        }
        return result;
    }

    py::array_t<uint8_t> render_enlarged(int count) const {
        wait_for_insertion_count(count);
        if (count > std::numeric_limits<int>::max() - (horizontal_ ? height_ : width_))
            throw py::value_error("enlarged image dimension is outside the supported range");
        const int output_width = width_ + (horizontal_ ? 0 : count);
        const int output_height = height_ + (horizontal_ ? count : 0);
        py::array_t<uint8_t> result({output_height, output_width, channels_});
        auto* pixels = result.mutable_data();
        {
            py::gil_scoped_release release;
            Image expanded{width_, height_, channels_, original_, {}, {}};
            std::vector<uint8_t> selected(static_cast<size_t>(width_) * height_, 0);
            for (size_t i = 0; i < static_cast<size_t>(count) * seam_length_; ++i)
                selected[ordered_pixels_[i]] = 1;
            if (horizontal_) transpose(expanded);
            if (horizontal_) {
                // Selection indices must follow the transposed source layout.
                std::vector<uint8_t> transposed(selected.size());
                for (int row = 0; row < height_; ++row)
                    for (int col = 0; col < width_; ++col)
                        transposed[static_cast<size_t>(col) * height_ + row] = selected[static_cast<size_t>(row) * width_ + col];
                selected.swap(transposed);
            }
            insert_selected(expanded, selected, count);
            if (horizontal_) transpose(expanded);
            std::memcpy(pixels, expanded.pixels.data(), expanded.pixels.size());
        }
        return result;
    }

    py::array_t<uint64_t> seam_positions_batch(int first, int limit) const {
        // The UI asks for a small prefix; wait only for its first missing seam.
        if (first < 1 || first > total_ || limit < 1)
            throw py::value_error("requested seam batch is outside the available image dimension");
        wait_for_count(first);
        int available;
        {
            std::lock_guard<std::mutex> guard(mutex_);
            available = std::min(limit, computed_ - first + 1);
        }
        py::array_t<uint64_t> result({available, seam_length_});
        auto* positions = result.mutable_data();
        {
            // The published prefix never changes; the worker may fill later
            // slots while this batch is copied without holding its mutex.
            py::gil_scoped_release release;
            const size_t offset = static_cast<size_t>(first - 1) * seam_length_;
            const size_t length = static_cast<size_t>(available) * seam_length_;
            for (size_t i = 0; i < length; ++i)
                positions[i] = static_cast<uint64_t>(ordered_pixels_[offset + i]);
        }
        return result;
    }

    py::array_t<uint64_t> insertion_positions_batch(int first, int limit) const {
        if (first < 1 || first > total_ + 1 || limit < 1)
            throw py::value_error("requested insertion batch is outside the available image dimension");
        wait_for_insertion_count(first);
        int available;
        {
            std::lock_guard<std::mutex> guard(mutex_);
            const int published = computed_ + (insertion_ready_ ? 1 : 0);
            available = std::min(limit, published - first + 1);
        }
        py::array_t<uint64_t> result({available, seam_length_});
        auto* positions = result.mutable_data();
        {
            py::gil_scoped_release release;
            const size_t offset = static_cast<size_t>(first - 1) * seam_length_;
            const size_t length = static_cast<size_t>(available) * seam_length_;
            for (size_t i = 0; i < length; ++i)
                positions[i] = static_cast<uint64_t>(ordered_pixels_[offset + i]);
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

    void wait_for_insertion_count(int count) const {
        if (count < 0 || count > total_ + 1)
            throw py::value_error("requested insertion count is outside the available image dimension");
        {
            py::gil_scoped_release release;
            std::unique_lock<std::mutex> lock(mutex_);
            condition_.wait(lock, [&] {
                return (source_ready_ && (computed_ >= count || (count == total_ + 1 && insertion_ready_))) || done_;
            });
        }
        std::lock_guard<std::mutex> guard(mutex_);
        if (!error_.empty()) throw std::runtime_error(error_);
        if (computed_ < count && !(count == total_ + 1 && insertion_ready_))
            throw std::runtime_error("seam calculation was cancelled");
    }

    py::array input_; // Retain the source without copying on the image-load path.
    py::object mask_input_;
    EnergyMode mode_;
    int width_ = 0, height_ = 0, channels_ = 0, total_ = 0, seam_length_ = 0;
    bool horizontal_ = false;
    mutable std::mutex mutex_;
    mutable std::condition_variable condition_;
    std::atomic<bool> started_{false}, cancelled_{false};
    int computed_ = 0;
    bool done_ = false, source_ready_ = false, insertion_ready_ = false;
    std::string error_;
    std::vector<uint8_t> original_;
    std::vector<size_t> ordered_pixels_;
};

py::array_t<uint8_t> process(const py::array& input, const py::object& width,
                            const py::object& height, bool highlight, const std::string& energy) {
    // Synchronous public API: width first, then height on the resulting image.
    Image working = read_image(input);
    const int new_width = target_dimension(width, working.width, "new_width");
    const int new_height = target_dimension(height, working.height, "new_height");
    const EnergyMode mode = parse_energy_mode(energy);
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
        if (new_width < working.width)
            carve_width(working, new_width, highlight ? &marked : nullptr, workspace, mode);
        else if (new_width > working.width)
            enlarge_width(working, new_width, highlight ? &marked : nullptr, mode);
        if (working.height != new_height) {
            transpose(working);
            if (new_height < working.width)
                carve_width(working, new_height, highlight ? &marked : nullptr, workspace, mode);
            else
                enlarge_width(working, new_height, highlight ? &marked : nullptr, mode);
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
    m.doc() = "RGB/RGBA uint8 backward or forward-energy seam carving; shrink or enlarge to 2x.";
    m.def("highlight", [](const py::array& image, const py::object& width,
                           const py::object& height, const std::string& energy) {
        return process(image, width, height, true, energy);
    }, py::arg("input_image").noconvert(), py::arg("new_width"), py::arg("new_height"),
       py::arg("energy") = "backward",
    "Mark removed or insertion seams red at their original positions; preserve input dimensions and alpha.");
    m.def("modify", [](const py::array& image, const py::object& width,
                        const py::object& height, const std::string& energy) {
        return process(image, width, height, false, energy);
    }, py::arg("input_image").noconvert(), py::arg("new_width"), py::arg("new_height"),
       py::arg("energy") = "backward",
    "Remove or insert minimum-energy vertical seams, then horizontal seams, to the requested size.");
    py::class_<SeamOrder, std::shared_ptr<SeamOrder>>(m, "SeamOrder")
        .def(py::init<py::array, const std::string&, py::object, const std::string&>(),
             py::arg("image"), py::arg("direction"), py::arg("mask") = py::none(),
             py::arg("energy") = "backward")
        .def("compute_all", &SeamOrder::compute_all,
             "Calculate all seams incrementally; call from a background worker.")
        .def("cancel", &SeamOrder::cancel)
        .def("progress", &SeamOrder::progress)
        .def("render", &SeamOrder::render, py::arg("count"),
             "Wait for the requested seam prefix and return its original-image preview.")
        .def("render_overlay", &SeamOrder::render_overlay, py::arg("count"),
             "Wait for the requested seam prefix and return a transparent red overlay.")
        .def("render_modified", &SeamOrder::render_modified, py::arg("count"),
             "Wait for the requested seam prefix and return the resized image.")
        .def("render_insertion", &SeamOrder::render_insertion, py::arg("count"),
             "Mark the selected insertion seams on the original-size image.")
        .def("render_insertion_overlay", &SeamOrder::render_insertion_overlay, py::arg("count"),
             "Return a transparent overlay for the selected insertion seams.")
        .def("render_enlarged", &SeamOrder::render_enlarged, py::arg("count"),
             "Insert blended pixels beside the selected seams.")
        .def("seam_positions_batch", &SeamOrder::seam_positions_batch,
             py::arg("first"), py::arg("limit"),
             "Wait for the first requested removal seam and return published pixel positions.")
        .def("insertion_positions_batch", &SeamOrder::insertion_positions_batch,
             py::arg("first"), py::arg("limit"),
             "Return distinct insertion seam positions, including the last source column.");
}
