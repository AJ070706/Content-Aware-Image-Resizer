#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <array>
#include <cmath>
#include <vector>

namespace py = pybind11;

inline uint8_t& pixel_at(std::vector<uint8_t>& img, int width, int height, 
                         int channels, int row, int col, int c) {
    if(row < 0 || row >= height || col < 0 || col >= width || c < 0 || c >= channels) {
        throw std::out_of_range(
            "pixel_at: coordinates out of bounds"
        );
    }
    return img[row * width * channels + col * channels + c];
}

inline std::array<uint8_t, 3> get_pixel(const std::vector<uint8_t>& img, int width, 
                                        int height, int channels, int row, int col) {
    if(row < 0 || row >= height || col < 0 || col >= width || channels < 3) {
        throw std::out_of_range(
            "get_pixel: coordinates out of bounds or not enough channels"
        );
    }

    std::array<uint8_t, 3> pixel;
    for(int c = 0; c < 3; c++) {
        pixel[c] = img[row * width * channels + col * channels + c];
    }
    return pixel;
}

inline void set_pixel(std::vector<uint8_t>& img, int width, int height, int channels, 
                      int row, int col, const std::array<uint8_t,3>& value) {

    if(row < 0 || row >= height || col < 0 || col >= width || channels < 3) {
        throw std::out_of_range(
            "set_pixel: coordinates out of bounds or not enough channels"
        );
    }

    for(int c = 0; c < 3; c++) {
        img[row * width * channels + col * channels + c] = value[c];
    }
}

inline int pixel_energy(const std::vector<uint8_t>& img, int width, int height, 
                        int channels, int row, int col) {
    int left  = (col == 0) ? col : col - 1;
    int right = (col == width - 1) ? col : col + 1;
    int up    = (row == 0) ? row : row - 1;
    int down  = (row == height - 1) ? row : row + 1;

    auto px_left  = get_pixel(img, width, height, channels, row, left);
    auto px_right = get_pixel(img, width, height, channels, row, right);
    auto px_up    = get_pixel(img, width, height, channels, up, col);
    auto px_down  = get_pixel(img, width, height, channels, down, col);

    int dx = 0, dy = 0;
    for(int c = 0; c < 3; ++c) {
        dx += std::abs((int)px_right[c] - (int)px_left[c]);
        dy += std::abs((int)px_down[c] - (int)px_up[c]);
    }
    return dx + dy;
}

std::vector<int> compute_energy_map(const std::vector<uint8_t>& img, int width, 
                                    int height, int channels) {
    std::vector<int> energy(height * width, 0);

    for (int row = 0; row < height; row++) {
        for (int col = 0; col < width; col++) {
            energy[row * width + col] 
                = pixel_energy(img, width, height, channels, row, col);
        }
    }
    return energy;
}

std::vector<int> compute_cumulative_map(const std::vector<int>& energy, 
                                        int width, int height) {
    std::vector<int> cost = energy;

    for (int row = 1; row < height; row++) {
        cost[row * width] += std::min(cost[(row - 1) * width],
                                      cost[(row - 1) * width + 1]);
        for (int col = 1; col < width - 1; col++) {
            cost[row * width + col] += std::min({cost[(row - 1) * width + col - 1],
                                                 cost[(row - 1) * width + col],
                                                 cost[(row - 1) * width + col + 1]});
        }
        cost[row * width + width - 1] += std::min(cost[(row - 1) * width + width - 2],
                                                  cost[(row - 1) * width + width - 1]);
    }
    return cost;
}

std::vector<int> find_vertical_seam(const std::vector<uint8_t>& img, int width, int height, int channels) {
    auto energy = compute_energy_map(img, width, height, channels);
    
    auto cost = compute_cumulative_map(energy, width, height);

    std::vector<int> seam(height, 0);

    int minCol = 0;
    int minVal = cost[(height - 1) * width];
    for (int col = 1; col < width; col++) {
        if (cost[(height - 1) * width + col] < minVal) {
            minVal = cost[(height - 1) * width + col];
            minCol = col;
        }
    }
    seam[height - 1] = minCol;

    for (int row = height - 2; row >= 0; row--) {
        int prevCol = seam[row + 1];
        int bestCol = seam[row + 1];

        if (prevCol > 0 && cost[row * width + prevCol - 1] 
                         < cost[row * width + prevCol]) {
            bestCol = prevCol - 1;
        }
        if (prevCol < width - 1 && cost[row * width + prevCol + 1] 
                                 < cost[row * width + prevCol]) {
            bestCol = prevCol + 1;
        }
        seam[row] = bestCol;
    }

    return seam;
}

void remove_vertical_seam(std::vector<uint8_t> &img, int& width, int height, int channels, const std::vector<int>& seam ) {
    int new_width = width - 1;
    std::vector<uint8_t> new_img(height * new_width * channels);

    for (int row = 0; row < height; row++) {
        int seam_col = seam[row];

        for (int col = 0; col < width; col++) {
            if (col == seam_col) continue;

            int new_col = (col < seam_col) ? col : col - 1;

            for (int c = 0; c < channels; c++) {
                new_img[row * new_width * channels +
                        new_col * channels + c]
                =
                img[row * width * channels +
                    col * channels + c];
            }
        }
    }

    img = std::move(new_img);
    width = new_width;
}

py::array_t<uint8_t> highlight(py::array_t<uint8_t> input_image, int new_width, int new_height) {
    py::buffer_info buf = input_image.request();
    int height = buf.shape[0];
    int width = buf.shape[1];
    int channels = buf.shape[2];
    uint8_t* ptr = static_cast<uint8_t*>(buf.ptr);

    std::vector<uint8_t> working(ptr, ptr + height*width*channels);

    std::vector<uint8_t> highlight_img(ptr, ptr + height*width*channels);

    int current_width = width;

    while (current_width > new_width) {
        std::vector<int> seam = find_vertical_seam(working, current_width, height, channels);

        for (int row = 0; row < height; row++) {
            int col = seam[row];
            highlight_img[row*width*channels + col*channels + 0] = 255; // Red
            highlight_img[row*width*channels + col*channels + 1] = 0;   // Green
            highlight_img[row*width*channels + col*channels + 2] = 0;   // Blue
        }

        remove_vertical_seam(working, current_width, height, channels, seam);
    }

    py::array_t<uint8_t> result({height, width, channels});
    std::memcpy(result.mutable_data(), highlight_img.data(), highlight_img.size());
    return result;
}

py::array_t<uint8_t> modify(py::array_t<uint8_t> input_image, int new_width, int new_height) {
    py::buffer_info buf = input_image.request();
    int height = buf.shape[0];
    int width = buf.shape[1];
    int channels = buf.shape[2];
    uint8_t* ptr = static_cast<uint8_t*>(buf.ptr);

    std::vector<uint8_t> working(ptr, ptr + height*width*channels);
    int current_width = width;

    while (current_width > new_width) {
        std::vector<int> seam = find_vertical_seam(working, current_width, height, channels);
        remove_vertical_seam(working, current_width, height, channels, seam);
        current_width--;
    }

    std::vector<uint8_t> working_r(height * new_width * channels, 0);

    for (int row = 0; row < height; row++) {
        for (int col = 0; col < new_width; col++) {
            working_r[row*new_width*channels + col*channels]
                = working[col*new_width*channels + row*channels];
            working_r[row*new_width*channels + col*channels + 1]
                = working[col*new_width*channels + row*channels + 1];
            working_r[row*new_width*channels + col*channels + 2]
                = working[col*new_width*channels + row*channels + 2];
        }
    }

    int current_height = height;

    while (current_height > new_height) {
        std::vector<int> seam = find_vertical_seam(working_r, current_height, new_width, channels);
        remove_vertical_seam(working_r, current_height, new_width, channels, seam);
        current_height--;
    }

    py::array_t<uint8_t> result({height, new_width, channels});
    std::memcpy(result.mutable_data(), working.data(), working.size());
    return result;
}

// Pybind11 module
PYBIND11_MODULE(main, m) {
    m.def("highlight", &highlight, "Highlight seams");
    m.def("modify", &modify, "Modify image");
}
